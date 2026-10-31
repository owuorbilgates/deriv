# Deriv Dynamic Match/Differs Quantitative Trading Bot

An automated quantitative trading system designed for **Deriv Volatility Indices** executing a **Dynamic Match/Differs Strategy** that eliminates Martingale account blowouts through asymmetric payout recovery.

---

## Strategy Architecture: Dynamic State Machine

```
               ┌────────────────────────────────────────────────────────┐
               │                                                        │
               ▼                                                        │
┌──────────────────────────────┐                          ┌─────────────┴────────────────┐
│      DIFFERS MODE            │      On 1 Loss           │        MATCH MODE            │
│  - Contract: DIGITDIFF       ├─────────────────────────►│  - Contract: DIGITMATCH      │
│  - Base Stake: $0.35         │   (Replaces 11x Martingale)│  - Stake: Flat $0.35 (No 11x)│
│  - Frequent small wins       │                          │  - Asymmetric 9x Payout      │
│  - Overrepresented digits    │                          │  - Trend-Following hot digit │
└──────────────┬───────────────┘                          └─────────────┬────────────────┘
               ▲                                                        │
               │                   On 1 Win in MATCH                    │
               └────────────────────────────────────────────────────────┘
```

### 1. Primary Phase: Differs (`DIGITDIFF`)
* **Execution:** Scans candidate Volatility Indices (`R_10` to `R_100`) via the 5-minute momentum rule.
* **Target:** Finds digits with frequency $\ge 18.0\%$ in the 100-tick window (avoiding normal variance).
* **Stake:** Flat **$\$0.35\text{ USD}$**.

### 2. Recovery Pivot: Match (`DIGITMATCH`)
* **Trigger:** The moment a loss occurs in Differs mode ($-\$0.35$).
* **Mechanism:** Instead of using an $11\times$ Martingale multiplier ($-\$3.85$) that risks blowing the account on a double loss, the bot **switches to Match mode**.
* **Anti-Revenge Guard:** Quarantines the losing digit in a 10-trade blacklist to prevent betting into the same cluster.
* **Stake:** Strictly flat **$\$0.35\text{ USD}$ (Zero Martingale)**.
* **Expectancy:** When a hot digit hits, `DIGITMATCH` pays $\sim 9.1\times$ total return ($+\$2.80\text{ to }+\$3.15\text{ USD}$ profit), completely erasing the prior loss in a single trade.

### 3. Restoration Phase
* **Trigger:** Upon any winning trade in Match mode.
* **Action:** The bot automatically transitions back to primary **Differs mode** with positive session net PnL!

---

## Configuration (`.env`)

```ini
# Deriv API Credentials
DERIV_API_TOKEN=your_deriv_personal_access_token_here
DERIV_APP_ID=your_registered_pat_app_id
DERIV_ACCOUNT_ID=your_options_account_id

# WebSocket Endpoint
DERIV_WS_URL=wss://api.derivws.com/trading/v1/options/ws/public

# Multi-Market Scanner Settings
DERIV_SCANNER_SYMBOLS=R_10,R_25,R_50,R_75,R_100
DERIV_SCANNER_INTERVAL=60

# Dynamic Strategy Settings
DERIV_STRATEGY_MODE=DIFFERS
DERIV_DYNAMIC_MODE_SWITCH=true
DERIV_DURATION=1
DERIV_STAKE_AMOUNT=0.35
DERIV_CURRENCY=USD
DERIV_DIGIT_WINDOW_SIZE=100
DERIV_DIGIT_THRESHOLD=18.0
DERIV_DIGIT_BLACKLIST_TRADES=10

# Risk Management (Martingale replaced by Match recovery)
DERIV_USE_MARTINGALE=false
DERIV_DAILY_STOP_LOSS=25.00
DERIV_DAILY_TAKE_PROFIT=5.00
DERIV_COOLDOWN_SECONDS=3.0

# Simulation Mode
DERIV_SIMULATION_MODE=false
```

---

## Quick Start

### 1. Run Live Bot
```powershell
python main.py
```

### 2. Run Paper-Trading Simulation Mode
```powershell
python main.py --sim
```

### 3. Run Unit Test Suite
```powershell
python -m unittest deriv/test_bot.py
```
