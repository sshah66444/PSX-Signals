"""
app.py
PSX AlphaSignals — Professional Screener, Signal Engine & Audited Ledger.
Built with real PSX market data, transparent condition checklists, and strict accounting.
"""

import streamlit as st
import pandas as pd
import plotly.express as px

from data_engine import get_watchlist, fetch_psx_stock, fetch_kse100_index
from signal_engine import generate_signal, format_actionable_card
from chart_engine import create_signal_chart
from backtester import (
    run_signal_backtest,
    run_sensitivity_grid,
    run_walk_forward_analysis,
)
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
        margin-bottom: 1.0rem;
    }
    .macro-banner-bull {
        background: linear-gradient(90deg, #064e3b, #047857);
        border: 1px solid #10b981;
        border-radius: 8px;
        padding: 12px 18px;
        margin-bottom: 1.2rem;
        color: #ecfdf5;
    }
    .macro-banner-bear {
        background: linear-gradient(90deg, #450a0a, #7f1d1d);
        border: 1px solid #ef4444;
        border-radius: 8px;
        padding: 12px 18px;
        margin-bottom: 1.2rem;
        color: #fef2f2;
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
st.sidebar.markdown("### 🛡️ Risk & Strategy Controls")
broker_fee = st.sidebar.slider("Broker Fee + Taxes (% round-trip):", min_value=0.10, max_value=1.0, value=0.35, step=0.05)
time_stop_limit = st.sidebar.slider("Time-Stop Exit (Bars Stalled):", min_value=0, max_value=10, value=4, step=1, help="Exits trades that stall for N bars without momentum follow-through.")
holding_limit = st.sidebar.slider("Max Holding Days (Backtest):", min_value=5, max_value=40, value=20, step=5)
apply_macro_gate = st.sidebar.checkbox("Enforce KSE-100 Macro Gate", value=True, help="Suppresses long breakout entries when KSE-100 is in correction.")
use_next_open = st.sidebar.checkbox("Next-Day Open Fills (T+1 Open)", value=True, help="Fills orders on bar T+1 Open price with volume-tiered spread & slippage rather than signal bar close.")
enforce_circuit = st.sidebar.checkbox("PSX Circuit Breakers (±7.5%)", value=True, help="Enforces PSX daily limit bounds; discards upper-locked opens and penalizes limit-down stop exits.")

@st.cache_data(ttl=900, show_spinner=False)
def get_cached_stock_data(symbol: str, period: str = "6mo", force_refresh: bool = False) -> dict:
    """Cached accessor for PSX market data with a 15-minute TTL."""
    return fetch_psx_stock(symbol, period=period, force_refresh=force_refresh)

@st.cache_data(ttl=900, show_spinner=False)
def get_cached_kse100(force_refresh: bool = False):
    """Cached accessor for KSE-100 index timeseries and macro regime."""
    return fetch_kse100_index(force_refresh=force_refresh)


# ----------------- DATA LOADING -----------------
with st.spinner(f"Fetching real market data for {active_symbol} and KSE-100 benchmark..."):
    stock_res = get_cached_stock_data(active_symbol, period=period)
    df_kse, regime_info = get_cached_kse100()

df_stock = stock_res.get("df")
data_source = stock_res.get("source", "None")
last_session = stock_res.get("last_date", "N/A")
data_age = stock_res.get("data_age_days", 999)

signal_data = generate_signal(
    active_symbol,
    df_stock,
    data_meta=stock_res,
    df_kse=df_kse,
    market_regime=regime_info if apply_macro_gate else None,
) if df_stock is not None else {}

# ----------------- HEADER & MACRO REGIME BANNER -----------------
company_info = watchlist.get(active_symbol, {"name": f"{active_symbol} (Custom PSX Stock)", "sector": "Equities"})

col_title, col_status = st.columns([3, 1])
with col_title:
    st.markdown('<div class="main-header">PSX AlphaSignals</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="sub-header">{company_info["name"]} | Sector: <b>{company_info["sector"]}</b> | Ticker: <b>{active_symbol}</b></div>', unsafe_allow_html=True)

with col_status:
    if stock_res.get("status") == "OK":
        st.caption(f"Stock Data: **{data_source}**")
        st.caption(f"Last Session: **{last_session}** (Age: {data_age}d)")
        if not stock_res.get("has_true_ohlc", True):
            st.warning("⚠️ **Approximated OHLC**: Data source provides Close/Open only. Setups requiring true intraday ranges (ATR, SL) are disqualified from TRIGGERED.")
    else:
        st.error(stock_res.get("error", "Data unavailable"))

# Macro Market Health Banner
if regime_info.get("is_bullish"):
    st.markdown(
        f"""
        <div class="macro-banner-bull">
            <b>🟢 Macro Market Regime: BULL MARKET</b><br>
            KSE-100 Closed at <b>{regime_info['close']:,.2f}</b> (above 50 EMA: <b>{regime_info['ema50']:,.2f}</b> | 200 EMA: <b>{regime_info['ema200']:,.2f}</b>).<br>
            <span style="font-size: 0.88rem; opacity: 0.95;">Institutional participation is favorable. Full breakout and pullback triggers enabled.</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
else:
    st.markdown(
        f"""
        <div class="macro-banner-bear">
            <b>🔴 Macro Market Regime: MARKET CORRECTION (Cash Preservation Mode)</b><br>
            KSE-100 Closed at <b>{regime_info['close']:,.2f}</b> (below 50 EMA: <b>{regime_info['ema50']:,.2f}</b>).<br>
            <span style="font-size: 0.88rem; opacity: 0.95;">Long breakouts are strictly suppressed across the screener. In a correcting market, cash is a valid defensive position.</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

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
            sig = generate_signal(
                sym,
                df_sym,
                data_meta=res,
                df_kse=df_kse,
                market_regime=regime_info if apply_macro_gate else None,
            )
            if sig:
                scan_records.append({
                    "Symbol": sym,
                    "Company": watchlist[sym]["name"],
                    "Sector": watchlist[sym]["sector"],
                    "Price (PKR)": sig["price"],
                    "Strategy": sig["strategy"],
                    "Status": sig["status"],
                    "RS vs KSE-100": "🟢 Leader" if sig.get("is_rs_leader") else "🔴 Lagging",
                    "Volatility Base": "🟢 Tight Base" if sig.get("is_squeeze") else "⚪ Loose",
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
                "RS vs KSE-100": "-",
                "Volatility Base": "-",
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
    st.markdown(f"### 🧪 Quantitative Backtesting & Statistical Validation for {active_symbol}")
    st.markdown(
        "Evaluates the **exact same deterministic strategy** as the live screener with institutional realism: "
        f"**Next-Day Open execution ({'Active' if use_next_open else 'Off'})**, "
        f"**PSX ±7.5% Circuit Breakers ({'Active' if enforce_circuit else 'Off'})**, "
        f"**Volume-Tiered Spread & Slippage**, and **{broker_fee}% commission**."
    )

    if df_stock is None:
        st.warning("Cannot backtest without verified historical market data.")
    else:
        bt_results = run_signal_backtest(
            df_stock,
            symbol=active_symbol,
            holding_max_bars=holding_limit,
            broker_fee_pct=broker_fee,
            df_kse=df_kse if apply_macro_gate else None,
            time_stop_bars=time_stop_limit,
            use_next_day_open=use_next_open,
            enforce_circuit_limits=enforce_circuit,
        )

        if "error" in bt_results:
            st.warning(bt_results["error"])
        elif bt_results.get("resolved_trades", 0) == 0 and bt_results.get("unresolved_trades", 0) == 0:
            st.info("No trade signals triggered for this stock during this historical lookback period.")
        else:
            # 1. Sample Size Adequacy & Microstructure Banner
            adequacy = bt_results.get("sample_adequacy", {})
            if adequacy.get("is_adequate"):
                st.success(f"{adequacy.get('badge')}: {adequacy.get('warning')}")
            else:
                st.warning(f"{adequacy.get('badge')}: {adequacy.get('warning')}")

            boot = bt_results.get("bootstrap", {})

            # 2. Metrics Row with 95% Confidence Intervals
            b1, b2, b3, b4, b5, b6 = st.columns(6)
            with b1:
                st.metric("Setups Triggered", bt_results["total_triggered"])
                st.caption(f"Resolved: {bt_results['resolved_trades']}")
            with b2:
                st.metric("TP1 Hit Rate", f"{bt_results['tp1_hit_rate_pct']}%")
                st.caption(f"95% CI: [{boot.get('tp1_rate_ci', (0, 0))[0]}% – {boot.get('tp1_rate_ci', (0, 0))[1]}%]")
            with b3:
                st.metric("TP2 Hit Rate", f"{bt_results['tp2_hit_rate_pct']}%")
                st.caption(f"Hits: {bt_results['tp2_hits']}")
            with b4:
                st.metric("Stop Loss Rate", f"{bt_results['stop_loss_rate_pct']}%")
                st.caption(f"Hits: {bt_results['sl_hits']}")
            with b5:
                st.metric("Time-Stop Exits", bt_results.get("time_stop_hits", 0))
                st.caption(f"Limit: {time_stop_limit} bars")
            with b6:
                st.metric("Net Strategy P&L", f"{bt_results['net_pnl_pct']:+.2f}%")
                st.caption(f"95% CI: [{boot.get('net_pnl_ci', (0, 0))[0]:+.1f}% – {boot.get('net_pnl_ci', (0, 0))[1]:+.1f}%]")

            st.caption(
                f"📊 **Profit Factor:** {bt_results.get('profit_factor', 0.0)} (95% CI: [{boot.get('profit_factor_ci', (0, 0))[0]} – {boot.get('profit_factor_ci', (0, 0))[1]}]) | "
                f"**Max Drawdown:** {bt_results.get('max_drawdown_pct', 0.0)}% | "
                f"**Avg Trade P&L:** {bt_results.get('avg_trade_pnl_pct', 0.0):+.2f}% | "
                f"**Execution Fill:** {'T+1 Open + Friction' if use_next_open else 'Bar Close'} | "
                f"**Macro Filter:** {'Active (KSE-100)' if apply_macro_gate else 'Disabled'}"
            )

            # Microstructure Discards notification
            c_discards = bt_results.get("circuit_lock_discards", 0)
            g_discards = bt_results.get("gap_discards", 0)
            if c_discards > 0 or g_discards > 0:
                st.info(
                    f"🛡️ **Realistic Microstructure Discards**: "
                    f"**{c_discards}** entry order(s) discarded due to Upper Circuit Lock (unfillable limit-up); "
                    f"**{g_discards}** entry order(s) skipped due to overnight gap past target or stop loss."
                )

            if bt_results["ambiguous_trades"] > 0:
                st.warning(
                    f"⚠️ **Ambiguous Candles Detected**: {bt_results['ambiguous_trades']} trade(s) touched both Target and Stop Loss "
                    "on the same day. These have been conservatively counted as stopped out."
                )

            trades_df = bt_results.get("trades_df")
            if trades_df is not None and not trades_df.empty:
                st.markdown("#### 📈 Simulated Equity Curve (Net of Broker Fees & Friction)")
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

                st.markdown("#### 📜 Historical Simulated Trade Log (Realistic Microstructure)")
                st.dataframe(trades_df, use_container_width=True, hide_index=True)

            # 3. Interactive Parameter Sensitivity Expander
            with st.expander("🔬 Parameter Sensitivity Matrix (Plateau vs. Needle Peak Analyzer)"):
                st.markdown(
                    "Pivots core strategy thresholds across a 9-point grid (RSI upper band, volume surge multiplier, and minimum R:R) "
                    "to test whether profitability is an **enduring parameter plateau** or an **overfitted needle peak**."
                )
                with st.spinner("Computing parameter sensitivity surface..."):
                    grid_res = run_sensitivity_grid(
                        df_stock,
                        symbol=active_symbol,
                        df_kse=df_kse if apply_macro_gate else None,
                        holding_max_bars=holding_limit,
                        time_stop_bars=time_stop_limit,
                    )
                st.markdown(f"**Stability Rating:** {grid_res['stability_badge']}")
                st.caption(
                    f"**Profitable Variations:** {grid_res['profitable_pct']}% | "
                    f"**Mean Net P&L:** {grid_res['mean_pnl']:+.2f}% | "
                    f"**Std Dev:** ±{grid_res['std_pnl']}%"
                )
                st.markdown(f"*{grid_res['assessment']}*")
                st.dataframe(grid_res["grid_df"], use_container_width=True, hide_index=True)

            # 4. Walk-Forward Cross-Validation Expander
            with st.expander("🔄 Rolling Walk-Forward Analysis (Out-of-Sample Forward Validation)"):
                st.markdown(
                    "Simulates rolling walk-forward optimization: trains in-sample on historical segments and tests forward "
                    "on subsequent unseen sessions to detect lookahead bias and curve-fitting."
                )
                with st.spinner("Evaluating rolling out-of-sample forward windows..."):
                    wfo_res = run_walk_forward_analysis(
                        df_stock,
                        symbol=active_symbol,
                        df_kse=df_kse if apply_macro_gate else None,
                    )
                if wfo_res["status"] == "INSUFFICIENT_HISTORY":
                    st.info(f"ℹ️ {wfo_res['message']}")
                else:
                    wf1, wf2, wf3 = st.columns(3)
                    with wf1:
                        st.metric("Forward Windows", wfo_res["windows_evaluated"])
                    with wf2:
                        st.metric("Out-of-Sample Trades", wfo_res["total_oos_trades"])
                    with wf3:
                        st.metric("Out-of-Sample Net P&L", f"{wfo_res['total_oos_net_pnl']:+.2f}%")
                    if not wfo_res["oos_trades_df"].empty:
                        st.dataframe(wfo_res["oos_trades_df"], use_container_width=True, hide_index=True)
                    else:
                        st.caption("No out-of-sample setups triggered in forward windows.")

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
    st.markdown("### 📚 Realities & Quantitative Edge of PSX Trading")
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

    #### 4. The Positive-Expectancy Edge (Institutional Quantitative Framework)
    * **Macro Market Gate (KSE-100)**: Over 75% of individual equity movements are tied to broad market beta. When the KSE-100 index trades below its 50 EMA, breakouts suffer high failure rates and pullbacks turn into deep traps. Suppressing long breakouts during corrections preserves cash and avoids severe drawdowns.
    * **Relative Strength (RS vs KSE-100)**: Institutional accumulation drives market leadership. We calculate the price ratio of each stock relative to the KSE-100 benchmark; only stocks demonstrating expanding relative strength are permitted to trigger.
    * **Volatility Compression Squeeze**: Breakouts that emerge from wide, erratic fluctuations have low follow-through. By measuring Bollinger Bandwidth compression against the 60-day range, we ensure entry occurs as volatility coils before an explosive directional thrust.
    * **Disciplined Time-Stops (4-Bar Rule)**: In ready market swing trading (T+2 settlement), genuine breakout momentum expands immediately. Positions that languish for 4 sessions without reaching TP1 are closed as stalled momentum, eliminating capital tie-up and preventing rolling declines.
    """)
