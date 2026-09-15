# PSX AlphaSignals — Technical Screener & Signal Generator

An interactive, high-performance web dashboard built with **Streamlit**, **Plotly**, and **Python** designed specifically for the Pakistan Stock Exchange (PSX).

The app demystifies and audits social media trade signals (such as the viral *"TP 1 2 3 hit Alhamdulilah"* posts) by generating mathematically grounded trade setups, computing real Risk-to-Reward ratios, visualizing support/resistance and indicator levels, scanning the KSE watchlist in bulk, and backtesting hit rates against historical data.

---

## Features

1. **Trade Signal Generator (Roman Urdu & English)**:
   - Evaluates Trend (20/50 EMAs), Momentum (MACD lines & histogram), Volatility (14 ATR), and Overbought/Oversold levels (14 RSI).
   - Generates exact formatted trade signal cards (complete with *Wajah*, *Caution*, *Buy Zone*, *Stop Loss*, and *TP1–TP4*).
   - Flags inverted risk-to-reward ratios before you execute.

2. **Interactive 3-Tier Technical Charts**:
   - Candlestick price action overlaid with 20 & 50 EMAs, shaded green Buy Zones, red dashed Stop Loss, and green dashed Take Profit targets.
   - Color-coded Volume bars with 20-period Moving Average.
   - Full MACD and RSI subplots.

3. **Multi-Stock Watchlist Screener**:
   - Scans 20+ leading PSX stocks (IPAK, OGDC, SYS, LUCK, HUBC, ENGRO, MCB, MEBL, FFC, PSO, etc.).
   - Interactive filtering by signal type (*Strong Buy*, *Risky/Extended Buy*, *Pullback*, *Neutral*) and sector.

4. **Historical Accuracy Auditor (Backtester)**:
   - Directly tests the empirical accuracy of these signals over historical bars.
   - Calculates the real hit rate for TP1 and TP2 versus Stop Loss.
   - Deducts realistic PSX broker commissions, SECP/CDC fees, and taxes to calculate net P&L.

5. **Dual Data Modes**:
   - **Live Feed**: Fetches real-time / EOD data from Yahoo Finance (`.KA` suffix).
   - **Offline Mode**: Includes deterministic PSX price model and local caching so the app works seamlessly even without an active internet connection.

---

## Getting Started

### 1. Install Dependencies
```bash
uv pip install -r requirements.txt
# Or standard pip:
pip install -r requirements.txt
```

### 2. Run the Web Application
```bash
streamlit run app.py
```
Open your browser at `http://localhost:8501`.

### 3. Run Automated Tests
```bash
python3 test_engine.py
```

---

## File Structure
- `app.py`: Main Streamlit web application.
- `data_engine.py`: Data retrieval, Yahoo Finance `.KA` integration, and local caching.
- `signal_engine.py`: Indicator math, signal classification, and Roman Urdu/English card generation.
- `chart_engine.py`: Interactive Plotly candlestick visualizer.
- `backtester.py`: Bar-by-bar accuracy auditor and equity curve simulator.
- `test_engine.py`: Automated test suite.
