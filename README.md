# Deriv Quantitative Digit Trading Bot (Separated Match & Differs with Auto-Market Switch on Loss)

An automated quantitative trading system designed for **Deriv Volatility Indices** executing standalone **DIFFERS** or **MATCH** strategies. At startup, the user chooses which strategy to execute. When a trade incurs a loss, the bot **automatically scans all 13 Volatility Indices for the highest momentum leader**, switches active instruments, and **continues on the same strategy**.

---

## Strategy Selection at Startup

When starting the bot without `--mode`, you are interactively prompted:
```text
====================================================================
🎯 SELECT TRADING STRATEGY
====================================================================
  [1] DIFFERS  - Bet on Cold Digits (<= 2.0% frequency)
  [2] MATCH    - Bet on Hot Digits  (>= 19.0% frequency)
====================================================================
Enter choice [1 or 2] (default: 1 - DIFFERS): 
```
Or pass the mode directly in CLI: `python main.py --mode 1` (or `--mode DIFFERS`) / `python main.py --mode 2` (or `--mode MATCH`).

---

## Strategy Logic & Dynamic Loss Handling

1. **Option 1: DIFFERS Strategy (`DIGITDIFF`)**
   - **Trigger:** Bets against **cold digits** with occurrence $\le 2.0\%$ in the rolling 100-tick window.
   - **Stake:** Flat **$\$0.35\text{ USD}$** (No Martingale).
   - **On Win:** Continues trading DIFFERS on the active market.
   - **On Loss:** Quarantines the losing digit in the anti-revenge blacklist, **immediately re-scans all 13 Volatility Indices**, switches to the new top-momentum market, and **continues trading DIFFERS**.

2. **Option 2: MATCH Strategy (`DIGITMATCH`)**
   - **Trigger:** Bets WITH the streak on **hot digits** with occurrence $\ge 19.0\%$ in the rolling 100-tick window.
   - **Stake:** Flat **$\$0.35\text{ USD}$** (Captures $+810\%$ profit per win).
   - **On Win:** Continues trading MATCH on the active market.
   - **On Loss:** Quarantines the losing digit in the anti-revenge blacklist, **immediately re-scans all 13 Volatility Indices**, switches to the new top-momentum market, and **continues trading MATCH**.

---

## Three Execution Modes Available

| Execution Mode | Command | Real Deriv Charts & Ticks? | Real Money at Risk? | Description |
| :--- | :--- | :---: | :---: | :--- |
| **1. Real Deriv Simulation** | `python main.py --real-sim`<br>*(or `python main.py --paper`)* | **YES (Live WebSocket)** | **NO ($0.00 Risk)** | Connects via API token/App ID, checks **genuine 5-minute momentum and real live ticks** from Deriv charts, but executes orders **locally on your machine**. |
| **2. Offline Simulation** | `python main.py --sim` | **NO (Offline)** | **NO ($0.00 Risk)** | Runs a purely offline synthetic random-walk generator. Useful for testing logic without an internet connection. |
| **3. Live Trading** | `python main.py` | **YES (Live WebSocket)** | **YES (Real Funds)** | Places real buy orders on your Deriv options trading account (`ROT...`). |

---

## Configuration (`.env`)

```ini
# Deriv API Credentials
DERIV_API_TOKEN=your_deriv_personal_access_token_here
DERIV_APP_ID=your_registered_pat_app_id
DERIV_ACCOUNT_ID=your_options_account_id

# WebSocket Endpoint
DERIV_WS_URL=wss://api.derivws.com/trading/v1/options/ws/public

# Multi-Market Scanner Settings (All 13 Volatility Indices)
DERIV_SCANNER_SYMBOLS=1HZ10V,1HZ15V,1HZ25V,1HZ30V,1HZ50V,1HZ75V,1HZ90V,1HZ100V,R_10,R_25,R_50,R_75,R_100
DERIV_SCANNER_INTERVAL=60

# Dynamic Strategy Settings (Decoupled Triggers)
DERIV_STRATEGY_MODE=DIFFERS
DERIV_DIFFERS_COLD_THRESHOLD=2.0
DERIV_MATCH_HOT_THRESHOLD=19.0
DERIV_DYNAMIC_MODE_SWITCH=true
DERIV_MATCH_MAX_ATTEMPTS=2
DERIV_DURATION=1
DERIV_STAKE_AMOUNT=0.35
DERIV_CURRENCY=USD
DERIV_DIGIT_WINDOW_SIZE=100
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

```powershell
# 1. Real Deriv Simulation (Real ticks, local paper orders)
python main.py --real-sim

# 2. Offline Simulation (Synthetic ticks, no internet required)
python main.py --sim

# 3. Live Trading (Real funds on Deriv account)
python main.py

# 4. Optional: Override Match Cap at runtime (e.g. 2 trades)
python main.py --match-cap 2
```
