# Deriv Dynamic Match/Differs Quantitative Trading Bot

An automated quantitative trading system designed for **Deriv Volatility Indices** executing a **Dynamic Match/Differs Strategy** that eliminates Martingale account blowouts through asymmetric payout recovery.

---

## Three Execution Modes Available

You can run the bot in any of the 3 modes below depending on your goal:

| Execution Mode | Command | Real Deriv Charts & Ticks? | Real Money at Risk? | Description |
| :--- | :--- | :---: | :---: | :--- |
| **1. Real Deriv Simulation** | `python main.py --real-sim`<br>*(or `python main.py --paper`)* | **YES (Live WebSocket)** | **NO ($0.00 Risk)** | Connects via API token/App ID, checks **genuine 5-minute momentum and real live ticks** from Deriv charts, but executes orders **locally on your machine**. |
| **2. Offline Simulation** | `python main.py --sim` | **NO (Offline)** | **NO ($0.00 Risk)** | Runs a purely offline synthetic random-walk generator. Useful for testing logic without an internet connection. |
| **3. Live Trading** | `python main.py` | **YES (Live WebSocket)** | **YES (Real Funds)** | Places real buy orders on your Deriv options trading account (`ROT...`). |

---

## Strategy Architecture: Dynamic Match/Differs State Machine

```
                        ┌────────────────────────────────────────────────────────┐
                        │                                                        │
                        ▼                                                        │
         ┌──────────────────────────────┐                          ┌─────────────┴────────────────┐
         │      DIFFERS MODE            │      On 1 Loss           │        MATCH MODE            │
         │  - Primary normal trading    ├─────────────────────────►│  - Recovery trading         │
         │  - Contract: DIGITDIFF       │   (NO 11x Martingale!)   │  - Contract: DIGITMATCH      │
         │  - Flat Stake: $0.35 USD     │                          │  - Flat Stake: $0.35 USD     │
         │  - Frequent small wins       │                          │  - Asymmetric 9.1x Payout    │
         └──────────────┬───────────────┘                          └─────────────┬────────────────┘
                        ▲                                                        │
                        │                   On 1 Win in MATCH                    │
                        └────────────────────────────────────────────────────────┘
```

1. **Primary Phase: Differs (`DIGITDIFF`)**
   * Starts in Differs mode.
   * Scans candidate markets (`R_10` to `R_100`) via the 5-minute momentum rule.
   * Trades overrepresented digits ($\ge 18\%$) with flat **$\$0.35\text{ USD}$**.

2. **Recovery Pivot: Match (`DIGITMATCH`)**
   * **Trigger:** The moment a loss occurs in Differs mode ($-\$0.35$).
   * **Mechanism:** Instead of applying an $11\times$ Martingale multiplier ($-\$3.85$), the bot **switches to Match mode**.
   * **Anti-Revenge Guard:** Quarantines the losing digit in a 10-trade blacklist to prevent betting into the same cluster.
   * **Stake:** Strictly flat **$\$0.35\text{ USD}$ (Zero Martingale)**.
   * **Expectancy:** When a hot digit hits, `DIGITMATCH` pays $\sim 9.1\times$ total return ($+\$2.80\text{ to }+\$3.15\text{ USD}$ profit), completely erasing the prior loss in a single trade.

3. **Restoration Phase**
   * **Trigger:** Upon any winning trade in Match mode.
   * **Action:** The bot automatically transitions back to primary **Differs mode** with positive net PnL!

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

# Risk Management
DERIV_USE_MARTINGALE=false
DERIV_DAILY_STOP_LOSS=25.00
DERIV_DAILY_TAKE_PROFIT=5.00
DERIV_COOLDOWN_SECONDS=3.0

# Virtual Bankroll for Real-Deriv Simulation / Paper Trading
DERIV_PAPER_BALANCE=100.00
```

---

## Commands to Run

### 1. Real Deriv Simulation (Live Deriv Ticks & Charts, Local Orders)
```powershell
python main.py --real-sim
# or
python main.py --paper
```

### 2. Offline Simulation (Synthetic Ticks, No Internet Required)
```powershell
python main.py --sim
```

### 3. Live Trading (Real Money Contracts on Deriv Account)
```powershell
python main.py
```

### 4. Run Unit Test Suite
```powershell
python -m unittest deriv/test_bot.py
```
