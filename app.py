"""
app.py
PSX AlphaSignals — Technical Screener & Signal Generator Web Dashboard.
"""

import streamlit as st
import pandas as pd
import plotly.express as px

from data_engine import get_watchlist, fetch_psx_stock
from signal_engine import generate_signal
from chart_engine import create_signal_chart
from backtester import run_signal_backtest

# Set page configuration
st.set_page_config(
    page_title="PSX AlphaSignals | Screener & Signal Engine",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom Styling
st.markdown("""
<style>
    .main-header {
        font-size: 2.2rem;
        font-weight: 700;
        background: linear-gradient(90deg, #00c853, #00b0ff);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 0.2rem;
    }
    .sub-header {
        color: #90a4ae;
        font-size: 0.95rem;
        margin-bottom: 1.5rem;
    }
    .metric-card {
        background-color: #1e293b;
        border-radius: 10px;
        padding: 16px;
        border: 1px solid #334155;
        text-align: center;
    }
    .signal-box {
        background-color: #0f172a;
        border: 1px solid #1e293b;
        border-left: 4px solid #00e676;
        border-radius: 8px;
        padding: 16px;
        font-family: 'Courier New', Courier, monospace;
        color: #eceff1;
        white-space: pre-wrap;
        font-size: 0.88rem;
        line-height: 1.5;
    }
    .tag-buy {
        background-color: #1b5e20;
        color: #a5d6a7;
        padding: 4px 10px;
        border-radius: 6px;
        font-weight: 600;
        display: inline-block;
    }
    .tag-risky {
        background-color: #bf360c;
        color: #ffccbc;
        padding: 4px 10px;
        border-radius: 6px;
        font-weight: 600;
        display: inline-block;
    }
    .tag-neutral {
        background-color: #37474f;
        color: #cfd8dc;
        padding: 4px 10px;
        border-radius: 6px;
        font-weight: 600;
        display: inline-block;
    }
</style>
""", unsafe_allow_html=True)

# ----------------- SIDEBAR -----------------
st.sidebar.markdown("## ⚙️ PSX Engine Controls")
watchlist = get_watchlist()

# Data feed toggle
use_live = st.sidebar.toggle("🌐 Live PSX Feed (Yahoo / .KA)", value=True, help="Toggle between live market fetching and local offline data engine.")

# Stock selection (default to IPAK as seen in screenshot)
symbols = list(watchlist.keys())
default_index = symbols.index("IPAK") if "IPAK" in symbols else 0

selected_symbol = st.sidebar.selectbox(
    "Select PSX Stock:",
    options=symbols,
    index=default_index,
    format_func=lambda x: f"{x} - {watchlist[x]['name'][:28]}",
)

custom_ticker = st.sidebar.text_input("Or enter custom PSX symbol (e.g. MEBL):", "").strip().upper()
active_symbol = custom_ticker if custom_ticker else selected_symbol

period_options = {"3 Months": "3mo", "6 Months": "6mo", "1 Year": "1y"}
selected_period_label = st.sidebar.selectbox("Lookback Period:", list(period_options.keys()), index=1)
period = period_options[selected_period_label]

st.sidebar.markdown("---")
st.sidebar.markdown("### 🛡️ Risk Management Parameters")
broker_fee = st.sidebar.slider("Broker Fee + Taxes (% round-trip):", min_value=0.10, max_value=1.0, value=0.35, step=0.05)
holding_limit = st.sidebar.slider("Max Holding Days (Backtest):", min_value=5, max_value=40, value=20, step=5)

st.sidebar.markdown("---")
st.sidebar.info("💡 **Tip**: Select **IPAK** to compare this system's output directly with the social media signal card.")

# ----------------- DATA LOADING -----------------
with st.spinner(f"Loading data for {active_symbol}..."):
    df_stock, data_source = fetch_psx_stock(active_symbol, period=period, use_live=use_live)

signal_data = generate_signal(active_symbol, df_stock) if not df_stock.empty else {}

# ----------------- HEADER -----------------
company_info = watchlist.get(active_symbol, {"name": f"{active_symbol} (Custom PSX Stock)", "sector": "Equities"})

col_title, col_status = st.columns([3, 1])
with col_title:
    st.markdown('<div class="main-header">PSX AlphaSignals</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="sub-header">{company_info["name"]} | Sector: <b>{company_info["sector"]}</b> | Ticker: <b>{active_symbol}</b></div>', unsafe_allow_html=True)

with col_status:
    st.caption(f"Source: **{data_source}**")
    if signal_data:
        st.caption(f"Last Updated: {signal_data.get('date_time')}")

# ----------------- TABS -----------------
tab1, tab2, tab3, tab4 = st.tabs([
    "🎯 Stock Deep Dive & Signal Card",
    "📊 PSX Watchlist Screener",
    "🧪 Signal Accuracy Auditor (Backtester)",
    "📚 PSX Reality & Truth Guide",
])

# ----------------- TAB 1: STOCK DEEP DIVE -----------------
with tab1:
    if not signal_data:
        st.error(f"Could not compute signals for {active_symbol}. Please check historical data availability.")
    else:
        # Metric cards
        m1, m2, m3, m4, m5 = st.columns(5)
        with m1:
            st.metric("Current Price", f"PKR {signal_data['price']:.2f}")
        with m2:
            st.metric("Signal Status", signal_data["signal_type"])
        with m3:
            st.metric("Quality Rating", signal_data["stars"])
        with m4:
            st.metric("TP1 Risk:Reward", f"{signal_data['rr_tp1']}:1")
        with m5:
            st.metric("RSI (14)", f"{signal_data['rsi']}")

        st.markdown("---")

        col_chart, col_card = st.columns([1.6, 1.0])

        with col_chart:
            st.markdown("#### 📈 Interactive Technical Chart")
            fig = create_signal_chart(signal_data["df_indicators"], signal_data, active_symbol)
            st.plotly_chart(fig, use_container_width=True)

            # Indicator Summary Table
            st.markdown("##### Key Indicator Metrics")
            ind_df = pd.DataFrame([{
                "Price": f"{signal_data['price']:.2f}",
                "20 EMA": f"{signal_data['ema20']:.2f}",
                "50 EMA": f"{signal_data['ema50']:.2f}",
                "MACD": f"{signal_data['macd']:.2f}",
                "MACD Signal": f"{signal_data['macd_signal']:.2f}",
                "Histogram": f"{signal_data['macd_hist']:.2f}",
                "RSI (14)": f"{signal_data['rsi']:.1f}",
                "ATR (14)": f"{signal_data['atr']:.2f}",
            }])
            st.dataframe(ind_df, use_container_width=True, hide_index=True)

        with col_card:
            st.markdown("#### 📱 Generated Trade Signal Card")
            st.caption("Matches the exact format and wording seen in PSX trading groups.")

            card_lang = st.radio("Card Language:", ["Roman Urdu (Social Style)", "English (Analytical)"], horizontal=True)

            if "Roman Urdu" in card_lang:
                card_text = signal_data["card_roman_urdu"]
            else:
                card_text = signal_data["card_english"]

            st.markdown(f'<div class="signal-box">{card_text}</div>', unsafe_allow_html=True)
            st.text_area("Raw Text for Copying / Sharing:", value=card_text, height=140)

            # Risk-Reward Inspection Alert
            st.markdown("##### ⚖️ Mathematical Risk vs Reward Audit")
            risk_pct = round((signal_data["risk_amount"] / signal_data["price"]) * 100, 1)
            tp1_pct = round((signal_data["reward_tp1"] / signal_data["price"]) * 100, 1)

            if signal_data["rr_tp1"] < 1.0:
                st.warning(
                    f"⚠️ **Inverted Risk-Reward Alert**: You are risking **{risk_pct}%** (to Stop Loss) to make **{tp1_pct}%** (on TP1). "
                    f"After PSX broker commissions ({broker_fee}%) and Capital Gains Tax, your net edge on TP1 is marginal."
                )
            else:
                st.success(
                    f"✅ **Favorable Setup**: Reward ({tp1_pct}%) exceeds Risk ({risk_pct}%). Ratio: **{signal_data['rr_tp1']}:1**"
                )

# ----------------- TAB 2: WATCHLIST SCREENER -----------------
with tab2:
    st.markdown("### 📊 PSX KSE Watchlist Screener")
    st.caption("Scans 20 leading PSX equities simultaneously and classifies signal states in real time.")

    if st.button("🔄 Scan Entire PSX Watchlist Now"):
        st.cache_data.clear()

    scan_records = []
    progress_bar = st.progress(0)
    symbols_list = list(watchlist.keys())

    for idx, sym in enumerate(symbols_list):
        progress_bar.progress((idx + 1) / len(symbols_list))
        df_sym, _ = fetch_psx_stock(sym, period="3mo", use_live=use_live)
        if not df_sym.empty and len(df_sym) > 20:
            sig = generate_signal(sym, df_sym)
            if sig:
                scan_records.append({
                    "Symbol": sym,
                    "Company": watchlist[sym]["name"],
                    "Sector": watchlist[sym]["sector"],
                    "Price (PKR)": sig["price"],
                    "Signal": sig["signal_type"],
                    "Stars": sig["stars"],
                    "Buy Zone": f"{sig['buy_zone_low']} - {sig['buy_zone_high']}",
                    "Stop Loss": sig["stop_loss"],
                    "TP1": sig["tp1"],
                    "R:R (TP1)": f"{sig['rr_tp1']}:1",
                    "RSI": sig["rsi"],
                })

    progress_bar.empty()

    if scan_records:
        df_scan = pd.DataFrame(scan_records)

        # Filters
        c_filter1, c_filter2 = st.columns([1, 1])
        with c_filter1:
            signal_filter = st.selectbox(
                "Filter by Signal Status:",
                ["All", "STRONG BUY", "BUY (Risky / Extended)", "BUY ON PULLBACK", "NEUTRAL / WAIT", "SELL / TAKE PROFIT"]
            )
        with c_filter2:
            sector_filter = st.selectbox(
                "Filter by Sector:",
                ["All"] + sorted(list(set(df_scan["Sector"])))
            )

        df_filtered = df_scan.copy()
        if signal_filter != "All":
            df_filtered = df_filtered[df_filtered["Signal"] == signal_filter]
        if sector_filter != "All":
            df_filtered = df_filtered[df_filtered["Sector"] == sector_filter]

        st.dataframe(
            df_filtered.style.highlight_max(subset=["RSI"], color="#3b1d1d")
                             .highlight_min(subset=["RSI"], color="#1d3b24"),
            use_container_width=True,
            hide_index=True,
        )
        st.caption(f"Showing {len(df_filtered)} of {len(df_scan)} PSX companies scanned.")

# ----------------- TAB 3: BACKTESTER & ACCURACY AUDITOR -----------------
with tab3:
    st.markdown(f"### 🧪 Historical Accuracy Auditor for {active_symbol}")
    st.markdown(
        "Audits the **true mathematical win rate** of these technical trade signals by simulating trades bar-by-bar "
        f"over historical PSX data, deducting **{broker_fee}% round-trip transaction costs**."
    )

    bt_results = run_signal_backtest(
        df_stock,
        symbol=active_symbol,
        holding_max_bars=holding_limit,
        broker_fee_pct=broker_fee,
    )

    if "error" in bt_results:
        st.warning(bt_results["error"])
    else:
        b1, b2, b3, b4 = st.columns(4)
        with b1:
            st.metric("Total Signals Generated", bt_results["total_trades"])
        with b2:
            st.metric("Win Rate (Hit TP1/TP2)", f"{bt_results['win_rate_pct']}%")
        with b3:
            st.metric("Stop Loss Hit Rate", f"{bt_results['sl_rate_pct']}%")
        with b4:
            net_color = "normal" if bt_results["total_net_pnl_pct"] >= 0 else "inverse"
            st.metric("Total Net P&L (%)", f"{bt_results['total_net_pnl_pct']}%", delta_color=net_color)

        c_graph, c_pie = st.columns([1.5, 1.0])

        with c_graph:
            st.markdown("#### Cumulative Trade Return (%)")
            trades_df = bt_results["trades_df"].copy()
            trades_df["Cumulative PnL (%)"] = trades_df["net_pnl_pct"].cumsum()
            fig_pnl = px.line(
                trades_df,
                x="exit_date",
                y="Cumulative PnL (%)",
                markers=True,
                title=f"{active_symbol} Equity Curve (Net of Fees)",
                template="plotly_dark",
            )
            fig_pnl.update_traces(line_color="#00e676", line_width=2.5)
            fig_pnl.update_layout(paper_bgcolor="#111722", plot_bgcolor="#161f30")
            st.plotly_chart(fig_pnl, use_container_width=True)

        with c_pie:
            st.markdown("#### Signal Outcome Breakdown")
            outcome_counts = trades_df["outcome"].value_counts().reset_index()
            outcome_counts.columns = ["Outcome", "Count"]
            fig_pie = px.pie(
                outcome_counts,
                names="Outcome",
                values="Count",
                color="Outcome",
                color_discrete_map={
                    "TP1 HIT (Target 1)": "#26a69a",
                    "TP2 HIT (Target 2)": "#00e676",
                    "STOP LOSS HIT": "#ef5350",
                    "EXPIRED (Time Limit)": "#78909c",
                },
                template="plotly_dark",
            )
            fig_pie.update_layout(paper_bgcolor="#111722", plot_bgcolor="#161f30")
            st.plotly_chart(fig_pie, use_container_width=True)

        st.markdown("#### 📜 Chronological Trade Log")
        display_log = trades_df[["entry_date", "entry_price", "exit_date", "exit_price", "outcome", "net_pnl_pct"]].copy()
        display_log.columns = ["Entry Date", "Entry (PKR)", "Exit Date", "Exit (PKR)", "Outcome", "Net PnL (%)"]
        st.dataframe(display_log, use_container_width=True, hide_index=True)

# ----------------- TAB 4: PSX REALITY & TRUTHS GUIDE -----------------
with tab4:
    st.markdown("### 📚 The Anatomy of PSX Social Trade Signals")
    st.markdown("""
    #### 1. Why Social Media Claims *"TP 1 2 3 hit Alhamdulilah"*
    * **The Law of Small Targets**: If a stock trades at 38 PKR and TP1 is placed at 39.5 PKR (+3.9%), ordinary daily market noise and fluctuations will frequently touch that level.
    * **Survivorship Bias**: Signal channels publish dozens of calls per month. The winning calls are watermarked and promoted heavily to sell VIP subscriptions; losing calls are quietly ignored or deleted.
    * **Double-talk Disclaimers**: Notice how the signal says *"BUY"*, but simultaneously says *"MACD bearish turn le chuka hai"* and *"price extended hai"*. This ensures the provider can take credit regardless of whether the price goes up or down.

    ---

    #### 2. The Inverted Risk-Reward Trap
    Many retail traders lose money even with a 50% or 60% win rate because of poor payoff geometry:
    * **Average Gain on TP1**: ~3.5%
    * **Average Loss on Stop Loss**: ~6.5%
    * If you win 6 out of 10 trades: $6 \times 3.5\% = +21\%$
    * If you lose 4 out of 10 trades: $4 \times 6.5\% = -26\%$
    * **Net Result**: **-5% Account Loss**, despite a 60% "Accuracy"!
    * Adding PSX broker commissions and Capital Gains Tax widens the deficit further.

    ---

    #### 3. What Actually Drives PSX Stock Prices?
    To trade PSX professionally, combine technical analysis with the factors institutional participants follow:
    * **NCCPL LIPI vs FIPI**: Track whether Local Mutual Funds, Banks, and Foreign Investors are net buyers. Institutional accumulation sets sustainable trends.
    * **Dividend Yields & Book Value**: PSX is heavily value- and dividend-driven. High-dividend paying blue chips (E&P, Fertilizers, Power, Commercial Banks) have strong valuation floors.
    * **Macro Policy**: State Bank of Pakistan (SBP) Monetary Policy Committee (MPC) rate announcements, T-bill cut-offs, and IMF tranche reviews dictate broad market direction.
    """)
