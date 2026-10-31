"""
Deriv WebSocket API Client (Dual Gateway: Modern OTP WebSocket + Legacy v3).

Handles:
- Modern OTP WebSocket trading gateway (api.derivws.com) for Personal Access Tokens (PAT).
- Multi-market tick history and candle fetching for momentum calculations.
- Real-time tick stream subscriptions and dynamic switching (forget / resubscribe).
- Request-Response multiplexing via req_id.
- Keepalive ping heartbeat and exponential backoff reconnection.
"""

import asyncio
import json
import logging
import random
import time
import urllib.request
import urllib.error
from typing import Any, Callable, Coroutine, Dict, List, Optional
import websockets
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger("DerivClient")


class DerivClient:
    """
    Robust Asynchronous WebSocket Client for Deriv API.
    Supports modern PAT tokens (OTP gateway) and legacy API tokens.
    """

    def __init__(
        self,
        app_id: str,
        api_token: str,
        ws_url_template: str = "wss://api.derivws.com/trading/v1/options/ws/public",
        account_id: Optional[str] = None,
        ping_interval: float = 30.0,
        reconnect_initial_delay: float = 2.0,
        reconnect_max_delay: float = 30.0,
        reconnect_factor: float = 2.0,
        max_reconnect_attempts: int = 20
    ):
        self.app_id = str(app_id)
        self.api_token = api_token
        self.target_account_id = account_id
        self.ws_url = ws_url_template
        self.ping_interval = ping_interval
        
        self.reconnect_initial_delay = reconnect_initial_delay
        self.reconnect_max_delay = reconnect_max_delay
        self.reconnect_factor = reconnect_factor
        self.max_reconnect_attempts = max_reconnect_attempts

        # Internal State
        self._ws: Optional[Any] = None
        self._is_connected: bool = False
        self._is_authorized: bool = False
        self._is_running: bool = False
        
        # Account Information
        self.account_info: Dict[str, Any] = {}
        self.balance: float = 0.0
        self.currency: str = "USD"
        self.login_id: str = ""

        # Request correlation via req_id
        self._req_counter: int = 1
        self._pending_requests: Dict[int, asyncio.Future] = {}

        # Subscriptions: {key: sub_id}
        self._active_subscriptions: Dict[str, str] = {}
        self._tick_symbol: Optional[str] = None

        # Event Callbacks
        self.on_tick: Optional[Callable[[Dict[str, Any]], Coroutine[Any, Any, None]]] = None
        self.on_contract_update: Optional[Callable[[Dict[str, Any]], Coroutine[Any, Any, None]]] = None

        # Background Tasks
        self._listener_task: Optional[asyncio.Task] = None
        self._ping_task: Optional[asyncio.Task] = None

    @property
    def is_connected(self) -> bool:
        return self._is_connected and self._ws is not None and not self._ws.closed

    @property
    def is_authorized(self) -> bool:
        return self._is_authorized

    def _next_req_id(self) -> int:
        req_id = self._req_counter
        self._req_counter += 1
        return req_id

    def _fetch_options_accounts(self) -> Optional[list]:
        """Fetch linked accounts via REST."""
        url = "https://api.derivws.com/trading/v1/options/accounts"
        headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Deriv-App-ID": self.app_id,
            "Accept": "application/json"
        }
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("data", [])
        except Exception as e:
            logger.warning(f"Failed to query options accounts: {e}")
            return None

    def _request_otp_url(self, account_id: str) -> Optional[str]:
        """Request OTP authenticated WebSocket URL via REST."""
        url = f"https://api.derivws.com/trading/v1/options/accounts/{account_id}/otp"
        headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Deriv-App-ID": self.app_id,
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        req = urllib.request.Request(url, data=b"{}", headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("data", {}).get("url") or data.get("websocket_url")
        except Exception as e:
            logger.warning(f"Failed to obtain OTP for {account_id}: {e}")
            return None

    async def connect(self) -> bool:
        """
        Connect to Deriv WebSocket gateway.
        If using a PAT token (starts with pat_), obtains an authenticated OTP URL directly.
        """
        # Modern PAT Token Pathway
        if self.api_token.startswith("pat_"):
            logger.info("Detected Personal Access Token (PAT). Querying active trading accounts...")
            accounts = self._fetch_options_accounts()
            if accounts:
                target_acc = None
                for a in accounts:
                    if self.target_account_id and a.get("account_id") == self.target_account_id:
                        target_acc = a
                        break
                    elif not target_acc and a.get("account_type") == "real":
                        target_acc = a
                selected = target_acc or accounts[0]
                self.login_id = selected.get("account_id")
                self.balance = float(selected.get("balance", 0.0))
                self.currency = selected.get("currency", "USD")
                self.account_info = selected
                logger.info(f"Targeting Account: [{self.login_id}] | Balance: ${self.balance:.2f} {self.currency}")

                otp_url = self._request_otp_url(self.login_id)
                if otp_url:
                    logger.info("Connecting to authenticated OTP WebSocket trading session...")
                    self._ws = await websockets.connect(otp_url, open_timeout=12.0, ping_interval=None)
                    self.ws_url = otp_url
                    self._is_connected = True
                    self._is_authorized = True
                    self._is_running = True
                    self._listener_task = asyncio.create_task(self._listen_loop(), name="DerivWSListener")
                    self._ping_task = asyncio.create_task(self._ping_loop(), name="DerivWSPing")
                    logger.info("✅ Connected and authenticated to Deriv Live Trading Gateway!")
                    return True

        # Candidate Gateways (Legacy or Public)
        candidate_endpoints = [
            f"wss://red.derivws.com/websockets/v3?app_id={self.app_id}&l=EN",
            "wss://api.derivws.com/trading/v1/options/ws/public"
        ]
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DerivTradingBot/2.0",
            "Origin": "https://deriv.com"
        }

        last_error = None
        for endpoint in candidate_endpoints:
            logger.info(f"Connecting to Deriv WebSocket gateway: {endpoint}...")
            try:
                self._ws = await websockets.connect(
                    endpoint,
                    additional_headers=headers,
                    open_timeout=8.0,
                    ping_interval=None
                )
                self.ws_url = endpoint
                self._is_connected = True
                logger.info(f"✅ Secure WebSocket connection established with {endpoint}.")

                self._is_running = True
                self._listener_task = asyncio.create_task(self._listen_loop(), name="DerivWSListener")
                self._ping_task = asyncio.create_task(self._ping_loop(), name="DerivWSPing")
                return True
            except Exception as e:
                logger.warning(f"Gateway {endpoint} unavailable ({type(e).__name__}: {e}). Trying next...")
                last_error = e

        self._is_connected = False
        raise ConnectionError(f"All Deriv WebSocket gateways failed. Last error: {last_error}")

    async def authorize(self) -> Dict[str, Any]:
        """Authenticate session (no-op if already authenticated via OTP URL)."""
        if self._is_authorized:
            return self.account_info

        if not self.is_connected:
            raise ConnectionError("Cannot authorize: WebSocket is not connected.")

        logger.info("Sending legacy authorization request...")
        payload = {"authorize": self.api_token, "req_id": self._next_req_id()}
        response = await self.send_request(payload)
        
        if "error" in response:
            err = response["error"]
            err_msg = f"Deriv API Authorization failed: [{err.get('code')}] {err.get('message')}"
            logger.critical(f"🛑 {err_msg}")
            self._is_authorized = False
            raise PermissionError(err_msg)

        auth_data = response.get("authorize", {})
        self.account_info = auth_data
        self.login_id = auth_data.get("loginid", "Unknown")
        self.balance = float(auth_data.get("balance", 0.0))
        self.currency = auth_data.get("currency", "USD")
        self._is_authorized = True
        logger.info(f"✅ Authorized successfully! Account: [{self.login_id}] | Balance: ${self.balance:.2f} {self.currency}")
        return auth_data

    async def fetch_candles(self, symbol: str, granularity: int = 60, count: int = 6) -> Dict[str, Any]:
        """Fetch OHLC candles for 5-minute momentum calculation."""
        payload = {
            "ticks_history": symbol,
            "end": "latest",
            "style": "candles",
            "granularity": granularity,
            "count": count,
            "req_id": self._next_req_id()
        }
        return await self.send_request(payload)

    async def fetch_ticks_history(self, symbol: str, count: int = 100) -> Dict[str, Any]:
        """Fetch historical ticks to pre-seed the 100-tick StrategyEngine buffer."""
        payload = {
            "ticks_history": symbol,
            "end": "latest",
            "style": "ticks",
            "count": count,
            "req_id": self._next_req_id()
        }
        return await self.send_request(payload)

    async def subscribe_ticks(self, symbol: str) -> None:
        """Subscribe to live tick stream for a synthetic volatility index."""
        self._tick_symbol = symbol
        logger.info(f"Subscribing to live tick stream for symbol: [{symbol}]...")
        payload = {
            "ticks": symbol,
            "subscribe": 1,
            "req_id": self._next_req_id()
        }
        response = await self.send_request(payload)
        if "error" in response:
            err = response["error"]
            logger.error(f"Failed to subscribe to ticks for {symbol}: {err.get('message')}")
            raise RuntimeError(f"Tick subscription failed: {err.get('message')}")

        sub = response.get("subscription", {})
        sub_id = sub.get("id")
        if sub_id:
            self._active_subscriptions[f"ticks_{symbol}"] = sub_id
            logger.info(f"✅ Subscribed to ticks for [{symbol}] (Subscription ID: {sub_id})")

    async def unsubscribe_ticks(self, symbol: str) -> None:
        """Unsubscribe from ticks for a given symbol."""
        key = f"ticks_{symbol}"
        sub_id = self._active_subscriptions.pop(key, None)
        if sub_id:
            logger.info(f"Unsubscribing from [{symbol}] (Sub ID: {sub_id})...")
            try:
                await self.send_request({"forget": sub_id, "req_id": self._next_req_id()})
                logger.info(f"✅ Successfully unsubscribed from [{symbol}].")
            except Exception as e:
                logger.warning(f"Notice: forget request for {symbol} returned: {e}")

    async def buy_contract(
        self,
        symbol: str,
        contract_type: str,
        stake: float,
        duration: int,
        barrier: int,
        duration_unit: str = "t",
        currency: str = "USD"
    ) -> Dict[str, Any]:
        """
        Request proposal and purchase contract on Deriv with real-time settlement subscription.
        """
        logger.info(
            f"🛒 Constructing BUY order: Type={contract_type} | Symbol={symbol} | "
            f"Stake=${stake:.2f} {currency} | Barrier={barrier} | Duration={duration}{duration_unit}"
        )

        # Step A: Request Price Proposal
        proposal_req = {
            "proposal": 1,
            "amount": float(stake),
            "basis": "stake",
            "contract_type": contract_type,
            "currency": currency,
            "underlying_symbol": symbol,
            "duration": int(duration),
            "duration_unit": duration_unit,
            "barrier": str(barrier),
            "req_id": self._next_req_id()
        }
        
        prop_res = await self.send_request(proposal_req)
        if "error" in prop_res:
            logger.error(f"❌ Proposal rejected by Deriv: {prop_res['error'].get('message')}")
            return prop_res

        proposal = prop_res.get("proposal", {})
        proposal_id = proposal.get("id")
        ask_price = float(proposal.get("ask_price", stake))
        payout = float(proposal.get("payout", 0.0))

        # Step B: Buy via Proposal ID with subscribe=1 to receive continuous updates
        buy_payload = {
            "buy": proposal_id,
            "price": ask_price,
            "subscribe": 1,
            "req_id": self._next_req_id()
        }
        response = await self.send_request(buy_payload)
        
        if "error" in response:
            err = response["error"]
            logger.error(f"❌ Order execution rejected by Deriv: [{err.get('code')}] {err.get('message')}")
            return response

        buy_info = response.get("buy", {})
        contract_id = buy_info.get("contract_id")
        balance_after = buy_info.get("balance_after")
        
        logger.info(
            f"🎯 Contract opened! ID: #{contract_id} | Expected Payout: ${payout:.2f} | "
            f"Balance After: ${balance_after}"
        )
        return response

    async def subscribe_open_contract(self, contract_id: int) -> None:
        """Subscribe to settlement updates for active contract."""
        payload = {
            "proposal_open_contract": 1,
            "contract_id": int(contract_id),
            "subscribe": 1,
            "req_id": self._next_req_id()
        }
        response = await self.send_request(payload)
        if "error" in response:
            err = response["error"]
            logger.warning(f"Notice on subscribe open contract #{contract_id}: {err.get('message')}")
            return

        sub_id = response.get("subscription", {}).get("id")
        if sub_id:
            self._active_subscriptions[f"contract_{contract_id}"] = sub_id

    async def send_request(self, payload: Dict[str, Any], timeout: float = 10.0) -> Dict[str, Any]:
        """Send JSON payload and await correlated response."""
        if not self.is_connected:
            raise ConnectionError("Cannot send request: WebSocket is disconnected.")

        req_id = payload.get("req_id")
        if req_id is None:
            req_id = self._next_req_id()
            payload["req_id"] = req_id

        future = asyncio.get_running_loop().create_future()
        self._pending_requests[req_id] = future

        try:
            await self._ws.send(json.dumps(payload))
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending_requests.pop(req_id, None)
            logger.error(f"Timeout waiting for response to req_id={req_id}")
            raise TimeoutError(f"Deriv API timed out waiting for response (req_id={req_id})")
        except Exception as e:
            self._pending_requests.pop(req_id, None)
            raise e

    async def _listen_loop(self) -> None:
        """Continuous listener loop routing incoming WebSocket messages."""
        logger.debug("Deriv WebSocket message listener loop started.")
        while self._is_running:
            try:
                if not self._ws:
                    break
                raw_message = await self._ws.recv()
                message: Dict[str, Any] = json.loads(raw_message)

                req_id = message.get("req_id")
                if req_id is not None and req_id in self._pending_requests:
                    fut = self._pending_requests.pop(req_id)
                    if not fut.done():
                        fut.set_result(message)

                msg_type = message.get("msg_type")
                if msg_type == "tick" and self.on_tick:
                    asyncio.create_task(self.on_tick(message["tick"]))
                elif msg_type == "proposal_open_contract" and self.on_contract_update:
                    asyncio.create_task(self.on_contract_update(message))

            except ConnectionClosed as e:
                logger.warning(f"Deriv WebSocket connection closed ({e.code}: {e.reason})")
                self._is_connected = False
                break
            except Exception as e:
                if self._is_running:
                    logger.error(f"Unexpected error in WS listener loop: {e}")
                break

    async def _ping_loop(self) -> None:
        """Heartbeat ping loop."""
        while self._is_running and self.is_connected:
            try:
                await asyncio.sleep(self.ping_interval)
                if self.is_connected:
                    await self._ws.send(json.dumps({"ping": 1, "req_id": self._next_req_id()}))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Ping transmission error: {e}")
                break

    async def reconnect_loop(self) -> bool:
        """Automatic exponential backoff reconnection loop."""
        logger.info("Entering automatic reconnection protocol...")
        delay = self.reconnect_initial_delay

        for attempt in range(1, self.max_reconnect_attempts + 1):
            if not self._is_running:
                return False
            logger.warning(f"Reconnection attempt {attempt}/{self.max_reconnect_attempts} in {delay:.1f}s...")
            await asyncio.sleep(delay)
            try:
                connected = await self.connect()
                if connected:
                    logger.info("✅ Reconnected successfully!")
                    if self._tick_symbol:
                        await self.subscribe_ticks(self._tick_symbol)
                    return True
            except Exception as e:
                logger.warning(f"Reconnection attempt {attempt} failed: {e}")
            delay = min(delay * self.reconnect_factor, self.reconnect_max_delay)

        logger.critical("Maximum reconnection attempts exhausted.")
        return False

    async def disconnect(self) -> None:
        """Gracefully disconnect."""
        logger.info("Disconnecting from Deriv WebSocket...")
        self._is_running = False
        self._is_connected = False
        self._is_authorized = False

        if self._ping_task and not self._ping_task.done():
            self._ping_task.cancel()
        if self._listener_task and not self._listener_task.done():
            self._listener_task.cancel()
        if self._ws and not self._ws.closed:
            await self._ws.close()
        logger.info("Deriv WebSocket disconnected.")
