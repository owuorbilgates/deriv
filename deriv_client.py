"""
Deriv WebSocket API Client (WebSocket v3).

Handles:
- Secure asynchronous WebSocket connection to Deriv API
- Token authentication and account authorization
- Real-time tick stream subscriptions
- Request-Response multiplexing via req_id
- Periodic heartbeat / ping keep-alive
- Automatic exponential backoff reconnection protocol
"""

import asyncio
import json
import logging
import random
import time
from typing import Any, Callable, Coroutine, Dict, Optional
import websockets
from websockets.exceptions import ConnectionClosed, InvalidStatus

logger = logging.getLogger("DerivClient")


class DerivClient:
    """
    Robust Asynchronous WebSocket Client for Deriv API v3.
    """

    def __init__(
        self,
        app_id: str,
        api_token: str,
        ws_url_template: str = "wss://ws.binaryws.com/websockets/v3?app_id={app_id}",
        ping_interval: float = 30.0,
        reconnect_initial_delay: float = 2.0,
        reconnect_max_delay: float = 30.0,
        reconnect_factor: float = 2.0,
        max_reconnect_attempts: int = 20
    ):
        """
        Initialize Deriv Client.
        
        Args:
            app_id: Deriv App ID (e.g., '1089' or custom registered id).
            api_token: Deriv API token with Trade permissions.
            ws_url_template: URL format string.
            ping_interval: Seconds between ping frames.
            reconnect_initial_delay: Initial backoff delay in seconds.
            reconnect_max_delay: Max delay cap in seconds.
            reconnect_factor: Exponential multiplier.
            max_reconnect_attempts: Max reconnection attempts before fatal exit.
        """
        self.app_id = str(app_id)
        self.api_token = api_token
        self.ws_url = ws_url_template.format(app_id=self.app_id)
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

        # Subscriptions
        self._active_subscriptions: Dict[str, str] = {}  # {type_or_key: subscription_id}
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

    async def connect(self) -> bool:
        """
        Establish WebSocket connection and start background listener and ping tasks.
        """
        logger.info(f"Connecting to Deriv WebSocket endpoint: {self.ws_url} (App ID: {self.app_id})...")
        
        # Set headers to maintain standard browser/client handshake compliance
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DerivTradingBot/2.0",
            "Origin": "https://deriv.com"
        }
        
        try:
            self._ws = await websockets.connect(
                self.ws_url,
                additional_headers=headers,
                open_timeout=15.0,
                ping_interval=None  # Managed manually by self._ping_loop()
            )
            self._is_connected = True
            logger.info("✅ Secure WebSocket connection established successfully.")

            # Start message consumer loop and heartbeat
            self._is_running = True
            self._listener_task = asyncio.create_task(self._listen_loop(), name="DerivWSListener")
            self._ping_task = asyncio.create_task(self._ping_loop(), name="DerivWSPing")

            return True
        except Exception as e:
            self._is_connected = False
            logger.error(f"Failed to connect to {self.ws_url}: {type(e).__name__} - {e}")
            raise

    async def authorize(self) -> Dict[str, Any]:
        """
        Send authorization payload to authenticate API token.
        
        Payload structure:
        {
            "authorize": "YOUR_API_TOKEN"
        }
        """
        if not self.is_connected:
            raise ConnectionError("Cannot authorize: WebSocket is not connected.")

        logger.info("Sending authorization request...")
        payload = {
            "authorize": self.api_token,
            "req_id": self._next_req_id()
        }

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

        # Mask token for security in logs
        masked_token = f"{self.api_token[:4]}...{self.api_token[-4:]}" if len(self.api_token) > 8 else "***"
        logger.info(
            f"✅ Authorized successfully! Account: [{self.login_id}] | "
            f"Balance: {self.balance:.2f} {self.currency} | Token: {masked_token}"
        )
        return auth_data

    async def subscribe_ticks(self, symbol: str) -> None:
        """
        Subscribe to live tick stream for a target synthetic volatility index.
        
        Payload structure:
        {
            "ticks": "R_100",
            "subscribe": 1
        }
        """
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
        Construct and send contract buy payload directly to Deriv API v3.
        
        Payload structure:
        {
            "buy": 1,
            "price": 1.00,
            "parameters": {
                "amount": 1.00,
                "basis": "stake",
                "contract_type": "DIGITMATCH" | "DIGITDIFF",
                "currency": "USD",
                "duration": 5,
                "duration_unit": "t",
                "symbol": "R_100",
                "barrier": "5"
            }
        }
        """
        logger.info(
            f"🛒 Constructing BUY order: Type={contract_type} | Symbol={symbol} | "
            f"Stake={stake:.2f} {currency} | Barrier={barrier} | Duration={duration}{duration_unit}"
        )

        payload = {
            "buy": 1,
            "price": float(stake),
            "parameters": {
                "amount": float(stake),
                "basis": "stake",
                "contract_type": contract_type,
                "currency": currency,
                "duration": int(duration),
                "duration_unit": duration_unit,
                "symbol": symbol,
                "barrier": str(barrier)
            },
            "req_id": self._next_req_id()
        }

        response = await self.send_request(payload)
        
        if "error" in response:
            err = response["error"]
            logger.error(f"❌ Order execution rejected by Deriv: [{err.get('code')}] {err.get('message')}")
            return response

        buy_info = response.get("buy", {})
        contract_id = buy_info.get("contract_id")
        payout = buy_info.get("payout")
        balance_after = buy_info.get("balance_after")
        
        logger.info(
            f"🎯 Contract opened! ID: #{contract_id} | Expected Payout: ${payout} | "
            f"Balance After: ${balance_after}"
        )
        return response

    async def subscribe_open_contract(self, contract_id: int) -> None:
        """
        Subscribe to live progress and settlement updates for an active contract.
        
        Payload structure:
        {
            "proposal_open_contract": 1,
            "contract_id": 12345678,
            "subscribe": 1
        }
        """
        payload = {
            "proposal_open_contract": 1,
            "contract_id": contract_id,
            "subscribe": 1,
            "req_id": self._next_req_id()
        }
        await self.send_raw(payload)

    async def unsubscribe(self, subscription_id: str) -> None:
        """Unsubscribe from a specific stream using subscription ID."""
        payload = {
            "forget": subscription_id,
            "req_id": self._next_req_id()
        }
        try:
            await self.send_request(payload)
        except Exception as e:
            logger.warning(f"Error unsubscribing {subscription_id}: {e}")

    async def send_raw(self, payload: Dict[str, Any]) -> None:
        """Send raw JSON payload over WebSocket."""
        if not self._ws or self._ws.closed:
            raise ConnectionError("Cannot send payload: WebSocket connection is closed.")
        message_str = json.dumps(payload)
        await self._ws.send(message_str)

    async def send_request(self, payload: Dict[str, Any], timeout: float = 15.0) -> Dict[str, Any]:
        """
        Send a request and wait asynchronously for its correlated response via req_id.
        """
        if "req_id" not in payload:
            payload["req_id"] = self._next_req_id()
        req_id = payload["req_id"]

        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending_requests[req_id] = future

        try:
            await self.send_raw(payload)
            response = await asyncio.wait_for(future, timeout=timeout)
            return response
        finally:
            self._pending_requests.pop(req_id, None)

    async def _listen_loop(self) -> None:
        """
        Continuous asynchronous receiver loop for incoming Deriv WebSocket messages.
        """
        logger.debug("Starting WebSocket message listener loop...")
        try:
            while self._is_running and self._ws and not self._ws.closed:
                try:
                    raw_msg = await self._ws.recv()
                    data = json.loads(raw_msg)
                except ConnectionClosed as cc:
                    logger.warning(f"WebSocket closed by server during recv: {cc}")
                    break
                except Exception as e:
                    logger.error(f"Error parsing incoming WebSocket message: {e}")
                    continue

                msg_type = data.get("msg_type")
                req_id = data.get("req_id")

                # Correlate response to pending send_request call
                if req_id is not None and req_id in self._pending_requests:
                    fut = self._pending_requests[req_id]
                    if not fut.done():
                        fut.set_result(data)

                # Route streaming events
                if msg_type == "tick" and self.on_tick:
                    try:
                        await self.on_tick(data)
                    except Exception as e:
                        logger.error(f"Error in on_tick callback: {e}", exc_info=True)

                elif msg_type == "proposal_open_contract" and self.on_contract_update:
                    try:
                        await self.on_contract_update(data)
                    except Exception as e:
                        logger.error(f"Error in on_contract_update callback: {e}", exc_info=True)

                elif msg_type == "ping":
                    logger.debug("Heartbeat pong received from Deriv server.")

                elif "error" in data and not req_id:
                    # Unsolicited server error
                    err = data["error"]
                    logger.error(f"Deriv server error received: [{err.get('code')}] {err.get('message')}")

        except asyncio.CancelledError:
            logger.debug("Listener loop task cancelled.")
        except Exception as e:
            logger.error(f"Unexpected exception in listener loop: {e}", exc_info=True)
        finally:
            self._is_connected = False
            logger.debug("Listener loop exited.")

    async def _ping_loop(self) -> None:
        """Periodic heartbeat frame to prevent connection drops."""
        try:
            while self._is_running and self._is_connected:
                await asyncio.sleep(self.ping_interval)
                if self.is_connected:
                    payload = {"ping": 1}
                    await self.send_raw(payload)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug(f"Ping loop error: {e}")

    async def reconnect_loop(self) -> bool:
        """
        ROBUST RECONNECTION PROTOCOL.
        
        Applies exponential backoff with jitter to recover from connection drops,
        re-authenticates with API token, and resubscribes to tick streams.
        """
        logger.warning("🔄 Initiating reconnection sequence...")
        await self._cleanup_connection()

        attempt = 1
        delay = self.reconnect_initial_delay

        while self._is_running and attempt <= self.max_reconnect_attempts:
            # Add small random jitter (0.5s to 1.5s) to prevent thundering herd
            jitter = random.uniform(0.5, 1.5)
            sleep_duration = min(delay + jitter, self.reconnect_max_delay)

            logger.info(
                f"Reconnection attempt {attempt}/{self.max_reconnect_attempts} in "
                f"{sleep_duration:.1f}s..."
            )
            await asyncio.sleep(sleep_duration)

            try:
                # 1. Establish new WebSocket transport
                await self.connect()

                # 2. Re-authenticate
                await self.authorize()

                # 3. Resubscribe to target tick stream if previously configured
                if self._tick_symbol:
                    await self.subscribe_ticks(self._tick_symbol)

                logger.info("🎉 Reconnection and re-authorization successful! Resuming operations.")
                return True

            except (ConnectionClosed, InvalidStatus, asyncio.TimeoutError, OSError) as net_err:
                logger.warning(
                    f"Reconnection attempt {attempt} failed: {type(net_err).__name__} ({net_err})"
                )
            except PermissionError as auth_err:
                logger.critical(f"Re-authorization failed with invalid credentials: {auth_err}")
                return False
            except Exception as unk_err:
                logger.error(f"Unexpected error during reconnection attempt {attempt}: {unk_err}")

            attempt += 1
            delay = min(delay * self.reconnect_factor, self.reconnect_max_delay)

        logger.critical(f"❌ Max reconnection attempts ({self.max_reconnect_attempts}) reached. Aborting.")
        return False

    async def _cleanup_connection(self) -> None:
        """Cleanly terminate background tasks and existing socket connection."""
        self._is_connected = False
        self._is_authorized = False

        # Cancel ping loop
        if self._ping_task and not self._ping_task.done():
            self._ping_task.cancel()

        # Cancel listener loop
        if self._listener_task and not self._listener_task.done():
            self._listener_task.cancel()

        # Close socket
        if self._ws and not self._ws.closed:
            try:
                await self._ws.close()
            except Exception:
                pass
        self._ws = None

        # Reject any pending awaiting futures
        for req_id, fut in list(self._pending_requests.items()):
            if not fut.done():
                fut.set_exception(ConnectionError("Connection closed before response received."))
        self._pending_requests.clear()

    async def disconnect(self) -> None:
        """Gracefully disconnect from Deriv API."""
        logger.info("Disconnecting from Deriv API...")
        self._is_running = False
        await self._cleanup_connection()
        logger.info("Disconnected cleanly.")
