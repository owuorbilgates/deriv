"""
Deriv Live Single Trade Execution Probe ($0.35 on Volatility 100 (1s) Index - DIGITDIFF).

Flow:
1. Connect to Deriv Public Gateway to capture live ticks on 1HZ100V.
2. Query /trading/v1/options/accounts to get active accounts and balance.
3. Request an authenticated OTP WebSocket URL for the target account (ROT91835717).
4. Request price proposal for DIGITDIFF on the barrier.
5. Execute the buy contract ($0.35 USD).
6. Stream contract progress until settlement and report profit/loss!
"""

import asyncio
import json
import logging
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path
from dotenv import load_dotenv

# Robust multi-location .env loader
for env_file in [
    Path.cwd() / ".env",
    Path(__file__).resolve().parent / ".env",
    Path(__file__).resolve().parent.parent / ".env"
]:
    if env_file.exists():
        load_dotenv(dotenv_path=env_file, override=False)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("DerivLive")

if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")


def get_trading_accounts(token: str, app_id: str):
    """Fetch active trading accounts and balances."""
    url = "https://api.derivws.com/trading/v1/options/accounts"
    headers = {
        "Authorization": f"Bearer {token}",
        "Deriv-App-ID": str(app_id),
        "Accept": "application/json"
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("data", []), None
    except Exception as e:
        return None, str(e)


def get_otp_websocket_url(account_id: str, token: str, app_id: str):
    """Generate authenticated OTP WebSocket URL."""
    url = f"https://api.derivws.com/trading/v1/options/accounts/{account_id}/otp"
    headers = {
        "Authorization": f"Bearer {token}",
        "Deriv-App-ID": str(app_id),
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    req = urllib.request.Request(url, data=b"{}", headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            ws_url = data.get("data", {}).get("url") or data.get("websocket_url")
            return ws_url, None
    except Exception as e:
        return None, str(e)


async def execute_live_test_trade():
    import websockets

    token = os.getenv("DERIV_API_TOKEN")
    app_id = os.getenv("DERIV_APP_ID") or "34PsAhlWu7nJZr8gtqGGd"
    target_account_id = os.getenv("DERIV_ACCOUNT_ID") or "ROT91835717"
    symbol = os.getenv("DERIV_SYMBOL") or "1HZ100V"
    stake = float(os.getenv("DERIV_STAKE_AMOUNT", "0.35"))
    duration = int(os.getenv("DERIV_DURATION", "5"))

    logger.info("=" * 72)
    logger.info("🚀 DERIV LIVE TEST TRADE EXECUTION PROBE")
    logger.info(f"Target Symbol:  {symbol}")
    logger.info(f"Contract Type:  DIGITDIFF (Differs)")
    logger.info(f"Stake Amount:   ${stake:.2f} USD")
    logger.info(f"Deriv-App-ID:   {app_id}")
    logger.info("=" * 72)

    if not token:
        logger.error("❌ DERIV_API_TOKEN is missing! Please set your token in .env.")
        return False

    # 1. Verify Accounts
    accounts, acc_err = get_trading_accounts(token, app_id)
    if acc_err or not accounts:
        logger.error(f"Failed to fetch accounts: {acc_err}")
        return False

    target_acc = None
    for acc in accounts:
        logger.info(
            f"👤 Found Account: {acc.get('account_id')} ({acc.get('account_type', '').upper()}) "
            f"| Balance: ${float(acc.get('balance', 0)):.2f} {acc.get('currency')} | Status: {acc.get('status')}"
        )
        if target_account_id and acc.get("account_id") == target_account_id:
            target_acc = acc
        elif not target_acc and acc.get("account_type") == "real":
            target_acc = acc

    if not target_acc:
        target_acc = accounts[0]

    account_id = target_acc.get("account_id")
    current_balance = float(target_acc.get("balance", 0.0))
    logger.info(f"🎯 Selected Target Account: {account_id} with Balance: ${current_balance:.2f} USD")

    if current_balance < stake:
        logger.error(f"❌ Insufficient funds in {account_id}: Balance ${current_balance:.2f} < Stake ${stake:.2f}")
        return False

    # 2. Capture live tick for barrier selection
    live_tick_url = os.getenv("DERIV_WS_URL") or "wss://api.derivws.com/trading/v1/options/ws/public"
    logger.info(f"Connecting to live public tick stream: {live_tick_url}...")
    
    last_digit = None
    async with websockets.connect(live_tick_url, open_timeout=10) as ws_pub:
        logger.info("✅ Connected to live public stream! Subscribing to ticks...")
        await ws_pub.send(json.dumps({"ticks": symbol, "subscribe": 1}))
        
        for i in range(3):
            raw = await asyncio.wait_for(ws_pub.recv(), timeout=5)
            msg = json.loads(raw)
            if msg.get("msg_type") == "tick":
                tick = msg["tick"]
                quote = float(tick["quote"])
                pip_size = int(tick.get("pip_size", 2))
                formatted = f"{quote:.{pip_size}f}"
                last_digit = int(formatted[-1])
                logger.info(f"📈 [LIVE TICK #{i+1}] Spot: {formatted} | Extracted Last Digit: [{last_digit}]")

    # Strategy: Predict last digit will DIFFER from the current last digit
    barrier = str(last_digit if last_digit is not None else 5)
    logger.info(f"🎯 Strategy Decision: Contract DIGITDIFF on Barrier '{barrier}' for {duration} ticks")

    # 3. Request OTP WebSocket URL
    logger.info(f"Requesting authenticated OTP WebSocket URL for {account_id}...")
    otp_url, otp_err = get_otp_websocket_url(account_id, token, app_id)
    if not otp_url:
        logger.error(f"Failed to get OTP WebSocket URL: {otp_err}")
        return False

    logger.info("✅ Authenticated OTP WebSocket URL received! Connecting to live trading session...")

    # 4. Open Authenticated Trading WebSocket Session
    async with websockets.connect(otp_url, open_timeout=12) as ws_trade:
        logger.info("✅ Authenticated real trading session connected!")

        # Step 4a: Request Price Proposal
        proposal_req = {
            "proposal": 1,
            "amount": stake,
            "basis": "stake",
            "contract_type": "DIGITDIFF",
            "currency": "USD",
            "underlying_symbol": symbol,
            "duration": duration,
            "duration_unit": "t",
            "barrier": barrier
        }
        logger.info(f"📤 Sending Price Proposal Request (Stake: ${stake}, Barrier: {barrier})...")
        await ws_trade.send(json.dumps(proposal_req))

        prop_raw = await asyncio.wait_for(ws_trade.recv(), timeout=8)
        prop_res = json.loads(prop_raw)
        
        if "error" in prop_res:
            logger.error(f"Proposal rejected: {prop_res['error'].get('message')}")
            return False

        proposal = prop_res.get("proposal", {})
        proposal_id = proposal.get("id")
        ask_price = float(proposal.get("ask_price", stake))
        payout = float(proposal.get("payout", 0.0))
        logger.info(f"📥 Proposal Accepted! ID: {proposal_id} | Ask: ${ask_price:.2f} | Potential Payout: ${payout:.2f}")

        # Step 4b: Buy the contract and subscribe to settlement updates
        buy_req = {
            "buy": proposal_id,
            "price": ask_price,
            "subscribe": 1
        }
        logger.info(f"🛒 PLACING LIVE BUY ORDER on {symbol}...")
        await ws_trade.send(json.dumps(buy_req))

        contract_id = None
        while True:
            msg_raw = await asyncio.wait_for(ws_trade.recv(), timeout=15)
            msg = json.loads(msg_raw)
            msg_type = msg.get("msg_type")

            if "error" in msg:
                logger.error(f"Order error: {msg['error'].get('message')}")
                return False

            if msg_type == "buy":
                buy_data = msg.get("buy", {})
                contract_id = buy_data.get("contract_id")
                balance_after = buy_data.get("balance_after")
                logger.info("=" * 72)
                logger.info("🎉 CONTRACT PURCHASED SUCCESSFULLY ON DERIV!")
                logger.info(f"Contract ID:    {contract_id}")
                logger.info(f"Transaction ID: {buy_data.get('transaction_id')}")
                logger.info(f"Buy Price:      ${buy_data.get('buy_price'):.2f} USD")
                logger.info(f"Balance After:  ${float(balance_after):.2f} USD")
                logger.info("=" * 72)
                logger.info(f"⏳ Monitoring live settlement over {duration} ticks...")

            elif msg_type == "proposal_open_contract":
                poc = msg.get("proposal_open_contract", {})
                is_sold = poc.get("is_sold", 0)
                status = poc.get("status")
                current_spot = poc.get("current_spot")
                profit = poc.get("profit")

                if is_sold:
                    logger.info("=" * 72)
                    if status == "won":
                        logger.info(f"🏆 CONTRACT WON! Net Profit: +${float(profit):.2f} USD")
                    else:
                        logger.info(f"📉 Contract Settled ({status.upper()}). Net Outcome: ${float(profit):.2f} USD")
                    logger.info(f"Exit Spot:     {current_spot}")
                    logger.info(f"Total Payout:  ${float(poc.get('payout', 0.0)):.2f} USD")
                    logger.info("=" * 72)
                    return True
                else:
                    tick_count = poc.get("tick_count", "?")
                    logger.info(f"📊 [Tick Progress] Current Spot: {current_spot} | Tick: {tick_count}/{duration}")

    return True


run_single_trade_test = execute_live_test_trade


if __name__ == "__main__":
    try:
        success = asyncio.run(execute_live_test_trade())
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\nTest stopped by user.")
