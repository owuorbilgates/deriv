# Deriv Dynamic Match/Differs Quantitative Trading Bot

An automated quantitative trading system designed for **Deriv Volatility Indices** executing a **Dynamic Match/Differs Strategy with a 2-Trade Match Cap** that eliminates Martingale blowouts and caps recovery drawdown.

---

## Three Execution Modes Available

| Execution Mode | Command | Real Deriv Charts & Ticks? | Real Money at Risk? | Description |
| :--- | :--- | :---: | :---: | :--- |
| **1. Real Deriv Simulation** | `python main.py --real-sim`<br>*(or `python main.py --paper`)* | **YES (Live WebSocket)** | **NO ($0.00 Risk)** | Connects via API token/App ID, checks **genuine 5-minute momentum and real live ticks** from Deriv charts, but executes orders **locally on your machine**. |
| **2. Offline Simulation** | `python main.py --sim` | **NO (Offline)** | **NO ($0.00 Risk)** | Runs a purely offline synthetic random-walk generator. Useful for testing logic without an internet connection. |
| **3. Live Trading** | `python main.py` | **YES (Live WebSocket)** | **YES (Real Funds)** | Places real buy orders on your Deriv options trading account (`ROT...`). |

---

## Strategy Architecture: Dynamic Match/Differs State Machine (2-Trade Match Cap)

```
                        ┌────────────────────────────────────────────────────────┐
                        │                                                        │
                        ▼                                                        │
         ┌──────────────────────────────┐                          ┌─────────────┴────────────────┐
         │      DIFFERS MODE            │      On 1 Loss           │        MATCH MODE            │
         │  - Primary normal trading    ├─────────────────────────►│  - Recovery trading         │
         │  - Contract: DIGITDIFF       │   (NO 11x Martingale!)   │  - Contract: DIGITMATCH      │
         │  - Flat Stake: $0.35 USD     │                          │  - Flat Stake: $0.35 USD     │
         │  - Frequent small wins       │                          │  - STRICT 2-TRADE CAP!       │
         └──────────────┬───────────────┘                          └─────────────┬────────────────┘
                        ▲                                                        │
                        │       1. On 1 WIN in Match (Recovery Won!)             │
                        └───────OR───────────────────────────────────────────────┘
                                2. On 2 LOSSES in Match (Cap Reached, Exit Drawdown)
```

1. **Primary Normal Phase: Differs (`DIGITDIFF`)**
   * Starts in Differs mode.
   * Scans all 13 Volatility Indices simultaneously via the 5-minute momentum rule:
     `abs((current_price - price_5m_ago) / price_5m_ago * 100)`.
   * Precision digit extraction respects the asset's exact pip size (2, 3, or 4 decimals).
   * Trades statistically overrepresented digits ($\ge 18\%$) with flat **$\$0.35\text{ USD}$**.

2. **Recovery Pivot: Match (`DIGITMATCH`)**
   * **Trigger:** The moment a loss occurs in Differs mode ($-\$0.35$).
   * **Mechanism:** Instead of applying an $11\times$ Martingale multiplier ($-\$3.85$), the bot **switches to Match mode**.
   * **Anti-Revenge Guard:** Quarantines the losing digit in a 10-trade blacklist to prevent betting into the same cluster.
   * **Stake:** Strictly flat **$\$0.35\text{ USD}$ (Zero Martingale)**.

3. **Strict 2-Trade Cap Protocol:**
   * **Win on Trade 1 or Trade 2:** Match payout ($\sim 9.1\times$ return, $+\$2.80\text{ to }+\$3.15\text{ profit}$) recovers the Differs loss; **immediately switches back to Differs mode**!
   * **Loss on Trade 1:** Bot attempts a second Match trade (`2/2`).
   * **Loss on Trade 2 (Cap Reached):** Bot terminates Match recovery immediately, caps drawdown to at most 2 Match trades ($-\$0.70$), and **switches back to Differs mode**!

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

# Dynamic Strategy Settings
DERIV_STRATEGY_MODE=DIFFERS
DERIV_DYNAMIC_MODE_SWITCH=true
DERIV_MATCH_MAX_ATTEMPTS=2
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
