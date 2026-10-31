# Deriv Quantitative Digit Trading Bot (Momentum-Driven Mean Reversion)

An asynchronous quantitative trading system designed for **Deriv Volatility Indices** implementing the **Momentum-Driven Mean Reversion Digit Differs (`DIGITDIFF`)** strategy with an **11.0x 2-Step Martingale Recovery Ladder**.

---

## Strategy Overview & Mathematical Architecture

### 1. Multi-Market Scanner (The 5-Minute Rule)
The bot simultaneously tracks candidate Deriv Volatility Indices:
`R_10`, `R_25`, `R_50`, `R_75`, `R_100`.

Every 60 seconds, the bot queries the 5-minute historical price movement for each index via `ticks_history`:
$$\text{Percentage Change} = \left| \frac{\text{Current Price} - \text{Price 5 Minutes Ago}}{\text{Price 5 Minutes Ago}} \right| \times 100$$

The index with the highest absolute percentage change becomes the active `target_symbol`. High momentum indicates prices are leaping across ranges rapidly, decreasing stall risks.

### 2. Digit Distribution & Selection Engine
- Maintains a rolling window of the last **100 ticks** for the active instrument.
- Extracts the exact trailing decimal digit of each tick using Python's `Decimal` module respecting market `pip_size` (avoiding floating-point truncation errors).
- Computes the occurrence frequency and percentage for all digits $0$ through $9$.
- **Mean Reversion Rule**: If any digit appears **$> 14.0\%$** of the time (expected frequency is $10.0\%$), that digit is statistically overrepresented. The bot selects this hot digit as the prediction barrier for a **`DIGITDIFF` (Differs)** contract, betting that its frequency must mathematically cool down.

### 3. Trade Execution
- **Contract Type:** `DIGITDIFF` (strictly Differs contracts).
- **Duration:** `1` tick (`1t`).
- **Barrier:** The overrepresented digit identified by the distribution engine.
- **Execution Lock:** Concurrency lock ensures only 1 trade is active at a time; settlement is monitored via `proposal_open_contract`.

### 4. Risk & Stake Management
- **Base Stake:** `$0.35 USD` per trade.
- **Recovery Protocol:** `11.0x` Martingale multiplier upon a loss (matching Differs payouts of ~9.9%).
- **2-Step Ladder Cap:**
  - **Step 0:** Base stake: `$0.35 USD`.
  - **Step 1 (After 1 loss):** Escalated stake: `$3.85 USD` ($0.35 \times 11.0$).
  - **Win at Step 0 or Step 1:** Profit realized; resets immediately to **Step 0 ($0.35 USD)**.
  - **Loss at Step 1 (Ladder Cap Reached):** Accepts the 2-step loss and resets immediately to **Step 0 ($0.35 USD)** to prevent account drawdown.
- **Hard Session Stops:**
  - `TARGET_PROFIT`: e.g. `+$5.00 USD` (terminates trading session upon reaching target).
  - `STOP_LOSS`: e.g. `-$25.00 USD` (terminates trading session upon hitting stop limit).
- **Balance Safeguard:** Verifies account balance before dispatching orders to prevent insufficient funds rejections.

---

## Configuration (`.env`)

Copy `.env.example` to `.env` and configure your credentials:

```ini
# Deriv API Credentials (obtain from Deriv API token settings)
DERIV_API_TOKEN=your_deriv_personal_access_token_here
DERIV_APP_ID=your_registered_pat_app_id
DERIV_ACCOUNT_ID=your_options_account_id

# WebSocket Endpoint
DERIV_WS_URL=wss://api.derivws.com/trading/v1/options/ws/public

# Multi-Market Scanner Settings
DERIV_SCANNER_SYMBOLS=R_10,R_25,R_50,R_75,R_100
DERIV_SCANNER_INTERVAL=60

# Strategy Settings
DERIV_CONTRACT_TYPE=DIGITDIFF
DERIV_DURATION=1
DERIV_STAKE_AMOUNT=0.35
DERIV_CURRENCY=USD
DERIV_DIGIT_WINDOW_SIZE=100
DERIV_DIGIT_THRESHOLD=14.0

# Risk Management
DERIV_MARTINGALE_MULTIPLIER=11.0
DERIV_MARTINGALE_MAX_STEPS=2
DERIV_DAILY_STOP_LOSS=25.00
DERIV_DAILY_TAKE_PROFIT=5.00
DERIV_COOLDOWN_SECONDS=3.0

# Simulation Mode
DERIV_SIMULATION_MODE=false
```

---

## Quick Start & Execution

### 1. Run Live Trading
```powershell
python main.py
```

### 2. Run Paper-Trading Simulation Mode
```powershell
python main.py --sim
```

### 3. CLI Overrides
```powershell
# Custom stake, target profit, and stop loss
python main.py --stake 0.35 --take-profit 5.00 --stop-loss 25.00

# Custom scanner interval (e.g. 30 seconds)
python main.py --scanner-interval 30
```

### 4. Run Unit Test Suite
```powershell
python -m unittest deriv/test_bot.py
```
