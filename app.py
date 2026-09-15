"""
app.py
PSX AlphaSignals — Professional Screener, Signal Engine & Audited Ledger.
Built with real PSX market data, transparent condition checklists, and strict accounting.
"""

import streamlit as st
import pandas as pd
import plotly.express as px

from data_engine import get_watchlist, fetch_psx_stock
from signal_engine import generate_signal, format_actionable_card
from chart_engine import create_signal_chart
from backtester import run_signal_backtest
from signal_tracker import get_active_signals, get_performance_summary

# Set page configuration
st.set_page_config(
    page_title="PSX AlphaSignals | Professional Screener & Signal Engine",
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
    .signal-card {
        background-color: #0f172a;
        border: 1px solid #1e293b;
        border-left: 4px solid #00e676;
        border-radius: 8px;
        padding: 16px;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        color: #eceff1;
        font-size: 0.92rem;
        line-height: 1.6;
    }
    .chk-pass {
        color: #00e676;
        font-weight: 600;
    }
    .chk-fail {
        color: #ef5350;
        font-weight: 600;
    }
</style>
""", unsafe_allow_html=True)

# ----------------- SIDEBAR -----------------
st.sidebar.markdown("## ⚙️ PSX Engine Controls")
watchlist = get_watchlist()

symbols = list(watchlist.keys())
default_index = symbols.index("OGDC") if "OGDC" in symbols else 0

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

@st.cache_data(ttl=900, show_spinner=False)
def get_cached_stock_data(symbol: str, period: str = "6mo", force_refresh: bool = False) -> dict:
    """Cached accessor for PSX market data with a 15-minute TTL."""
    return fetch_psx_stock(symbol, period=period, force_refresh=force_refresh)


# ----------------- DATA LOADING -----------------
with st.spinner(f"Fetching real market data for {active_symbol}..."):
    stock_res = get_cached_stock_data(active_symbol, period=period)

df_stock = stock_res.get("df")
data_source = stock_res.get("source", "None")
last_session = stock_res.get("last_date", "N/A")
data_age = stock_res.get("data_age_days", 999)

signal_data = generate_signal(active_symbol, df_stock, data_meta=stock_res) if df_stock is not None else {}

# ----------------- HEADER -----------------
company_info = watchlist.get(active_symbol, {"name": f"{active_symbol} (Custom PSX Stock)", "sector": "Equities"})

col_title, col_status = st.columns([3, 1])
with col_title:
    st.markdown('<div class="main-header">PSX AlphaSignals</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="sub-header">{company_info["name"]} | Sector: <b>{company_info["sector"]}</b> | Ticker: <b>{active_symbol}</b></div>', unsafe_allow_html=True)

with col_status:
    if stock_res.get("status") == "OK":
        st.caption(f"Source: **{data_source}**")
        st.caption(f"Last Session: **{last_session}** (Age: {data_age}d)")
        if not stock_res.get("has_true_ohlc", True):
            st.warning("⚠️ **Approximated OHLC**: Data source provides Close/Open only. Setups requiring true intraday ranges (ATR, SL) are disqualified from TRIGGERED.")
    else:
        st.error(stock_res.get("error", "Data unavailable"))

# ----------------- TABS -----------------
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "🎯 Actionable Setup & Checklist",
    "📊 Real PSX Watchlist Screener",
    "🧪 Historical Backtester (Rigorous)",
    "📜 Live Bot Ledger (signals.db)",
    "📚 PSX Reality & Truth Guide",
])

# ----------------- TAB 1: ACTIONABLE SETUP -----------------
with tab1:
    if not signal_data or df_stock is None:
        st.warning(f"Live market data unavailable for {active_symbol}. The system refuses to fabricate synthetic prices.")
    else:
        # Top Metrics Row
        m1, m2, m3, m4, m5 = st.columns(5)
        with m1:
            st.metric("Current Price", f"PKR {signal_data['price']:.2f}")
        with m2:
            st.metric("Strategy", signal_data["strategy"].title())
        with m3:
            st.metric("Status", signal_data["status"])
        with m4:
            st.metric("TP1 Reward:Risk", f"{signal_data.get('rr_tp1', 0)}:1")
        with m5:
            st.metric("RSI (14)", f"{signal_data['rsi']}")

        st.markdown("---")

        col_chart, col_card = st.columns([1.6, 1.1])

        with col_chart:
            st.markdown("#### 📈 Price Action & Key Levels")
            fig = create_signal_chart(signal_data["df_indicators"], signal_data, active_symbol)
            st.plotly_chart(fig, use_container_width=True)

        with col_card:
            st.markdown("#### 📋 Actionable Setup Card")
            card_html = format_actionable_card(signal_data, company_name=company_info["name"])
            st.markdown(f'<div class="signal-card">{card_html}</div>', unsafe_allow_html=True)

            st.markdown("---")
            st.markdown("#### 🔍 Condition Checklist")
            checklist = signal_data.get("checklist", {})
            for rule, passed in checklist.items():
                icon = "✅" if passed else "❌"
                cls = "chk-pass" if passed else "chk-fail"
                st.markdown(f"{icon} <span class='{cls}'>{rule}</span>", unsafe_allow_html=True)

            st.caption(f"Trigger Note: {signal_data.get('trigger_note')}")

# ----------------- TAB 2: WATCHLIST SCREENER -----------------
with tab2:
    symbols_list = list(watchlist.keys())
    st.markdown("### 📊 Real PSX Watchlist Screener")
    st.caption(f"Scans {len(symbols_list)} leading PSX equities across all core sectors using verified real prices.")

    force_refresh_scan = False
    if st.button("🔄 Force Refresh & Scan PSX Watchlist"):
        st.cache_data.clear()
        force_refresh_scan = True

    scan_records = []
    progress_bar = st.progress(0)

    for idx, sym in enumerate(symbols_list):
        progress_bar.progress((idx + 1) / len(symbols_list))
        res = get_cached_stock_data(sym, period="3mo", force_refresh=force_refresh_scan)
        if res.get("status") == "OK":
            df_sym = res["df"]
            sig = generate_signal(sym, df_sym, data_meta=res)
            if sig:
                scan_records.append({
                    "Symbol": sym,
                    "Company": watchlist[sym]["name"],
                    "Sector": watchlist[sym]["sector"],
                    "Price (PKR)": sig["price"],
                    "Strategy": sig["strategy"],
                    "Status": sig["status"],
                    "Entry Range": f"{sig.get('entry_min', 0):.2f} – {sig.get('entry_max', 0):.2f}",
                    "Stop Loss": sig.get("stop_loss", 0),
                    "TP1": sig.get("tp1", 0),
                    "R:R (TP1)": f"{sig.get('rr_tp1', 0)}:1",
                    "RSI": sig["rsi"],
                    "OHLC Integrity": "Verified True OHLC" if res.get("has_true_ohlc") else "Approximated (Open/Close)",
                    "Data Source": res["source"],
                })
        else:
            scan_records.append({
                "Symbol": sym,
                "Company": watchlist[sym]["name"],
                "Sector": watchlist[sym]["sector"],
                "Price (PKR)": "-",
                "Strategy": "-",
                "Status": "UNAVAILABLE",
                "Entry Range": "-",
                "Stop Loss": "-",
                "TP1": "-",
                "R:R (TP1)": "-",
                "RSI": "-",
                "OHLC Integrity": "Unavailable",
                "Data Source": "Offline / Skipped",
            })

    progress_bar.empty()

    if scan_records:
        df_scan = pd.DataFrame(scan_records)

        # Filters
        c_filter1, c_filter2 = st.columns([1, 1])
        with c_filter1:
            status_filter = st.selectbox(
                "Filter by Setup Status:",
                ["All", "TRIGGERED", "WATCHING", "NEUTRAL", "UNAVAILABLE"]
            )
        with c_filter2:
            strategy_filter = st.selectbox(
                "Filter by Strategy:",
                ["All", "BREAKOUT", "PULLBACK", "NONE"]
            )

        df_filtered = df_scan.copy()
        if status_filter != "All":
            df_filtered = df_filtered[df_filtered["Status"] == status_filter]
        if strategy_filter != "All":
            df_filtered = df_filtered[df_filtered["Strategy"] == strategy_filter]

        st.dataframe(df_filtered, use_container_width=True, hide_index=True)
        st.caption(f"Showing {len(df_filtered)} of {len(df_scan)} PSX companies.")

# ----------------- TAB 3: RIGOROUS BACKTESTER -----------------
with tab3:
    st.markdown(f"### 🧪 Rigorous Historical Simulation for {active_symbol}")
    st.markdown(
        "Uses the **exact same strategy rules** as the live screener. "
        f"Tracks independent TP1 & TP2 targets, detects ambiguous candles, and deducts **{broker_fee}% round-trip costs**."
    )

    if df_stock is None:
        st.warning("Cannot backtest without verified historical market data.")
    else:
        bt_results = run_signal_backtest(
            df_stock,
            symbol=active_symbol,
            holding_max_bars=holding_limit,
            broker_fee_pct=broker_fee,
        )

        if "error" in bt_results:
            st.warning(bt_results["error"])
        elif bt_results.get("resolved_trades", 0) == 0 and bt_results.get("unresolved_trades", 0) == 0:
            st.info("No trade signals triggered for this stock during this historical lookback period.")
        else:
            b1, b2, b3, b4, b5 = st.columns(5)
            with b1:
                st.metric("Total Setups Triggered", bt_results["total_triggered"])
            with b2:
                st.metric("TP1 Hit Rate", f"{bt_results['tp1_hit_rate_pct']}%")
            with b3:
                st.metric("TP2 Hit Rate", f"{bt_results['tp2_hit_rate_pct']}%")
            with b4:
                st.metric("Stop Loss Hit Rate", f"{bt_results['stop_loss_rate_pct']}%")
            with b5:
                st.metric("Net Strategy P&L", f"{bt_results['net_pnl_pct']:+.2f}%")

            if bt_results["ambiguous_trades"] > 0:
                st.warning(
                    f"⚠️ **Ambiguous Candles Detected**: {bt_results['ambiguous_trades']} trade(s) touched both Target and Stop Loss "
                    "on the same day. These have been conservatively counted as stopped out."
                )

            trades_df = bt_results.get("trades_df")
            if trades_df is not None and not trades_df.empty:
                st.markdown("#### 📈 Simulated Equity Curve (Net of Broker Fees)")
                trades_df["Cumulative PnL (%)"] = trades_df["net_pnl_pct"].cumsum()
                fig_pnl = px.line(
                    trades_df,
                    x="exit_date",
                    y="Cumulative PnL (%)",
                    markers=True,
                    template="plotly_dark",
                )
                fig_pnl.update_traces(line_color="#00e676", line_width=2.5)
                fig_pnl.update_layout(paper_bgcolor="#111722", plot_bgcolor="#161f30")
                st.plotly_chart(fig_pnl, use_container_width=True)

                st.markdown("#### 📜 Historical Simulated Trade Log")
                st.dataframe(trades_df, use_container_width=True, hide_index=True)

# ----------------- TAB 4: LIVE BOT LEDGER (SIGNALS.DB) -----------------
with tab4:
    st.markdown("### 📜 Live Persistent Signal Ledger (`signals.db`)")
    st.markdown(
        "Audited track record of real signals **actually issued by this bot** over time. "
        "Completely separated from historical backtests."
    )

    perf = get_performance_summary()
    active_setups = get_active_signals()

    l1, l2, l3, l4 = st.columns(4)
    with l1:
        st.metric("Total Setups Recorded", perf["total_recorded"])
    with l2:
        st.metric("Currently Active / Watching", perf["active_count"])
    with l3:
        st.metric("Closed Trades", perf.get("closed_count", 0))
    with l4:
        st.metric("Audited Win Rate", f"{perf.get('win_rate_pct', 0.0)}%")

    st.markdown("#### ⚡ Currently Active & Watching Setups")
    if active_setups:
        df_active = pd.DataFrame(active_setups)
        st.dataframe(df_active, use_container_width=True, hide_index=True)
    else:
        st.info("No active setups in progress.")

    st.markdown("#### 📜 Completed Ledger Trades")
    if "ledger_df" in perf and not perf["ledger_df"].empty:
        st.dataframe(perf["ledger_df"], use_container_width=True, hide_index=True)
    else:
        st.caption("No closed trades recorded in the live ledger yet.")

# ----------------- TAB 5: EDUCATIONAL GUIDE -----------------
with tab5:
    st.markdown("### 📚 Realities of PSX Trading")
    st.markdown("""
    #### 1. Why We Require Data Integrity
    Fabricated or synthetic data destroys trader trust. A trade alert is only as dependable as the quote on which it was generated.
    If PSX portals or market feeds are unreachable or delayed, this system explicitly marks data as unavailable rather than inventing numbers.

    #### 2. Why Backtests and Live Signals Must Match
    Many retail signal systems publish attractive backtests that use rules entirely different from the alerts sent to subscribers.
    Here, `evaluate_bar_strategy()` is the single mathematical engine evaluating both past bars and today's live close.

    #### 3. Managing Asymmetry
    * Small targets (2-3%) hit frequently due to market noise.
    * A system with 60% win rate can lose money if the average loss (-6%) is twice the size of the average gain (+3%).
    * Strict minimum Risk-to-Reward filters (>= 1.2:1 to 1.5:1) are mandatory to achieve long-term positive expectancy.
    """)
