"""
chart_engine.py
Generates interactive Plotly charts with Candlesticks, EMAs, Buy Zones,
Stop Loss, and TP level overlays, plus Volume and MACD subplots.
"""

import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd


def create_signal_chart(df: pd.DataFrame, signal_data: dict, symbol: str) -> go.Figure:
    """
    Creates an interactive 3-tier financial chart:
    1. Candlestick with EMAs, Buy Zone, Stop Loss & TP targets.
    2. Volume bars.
    3. MACD line, signal line, and histogram.
    """
    if df is None or df.empty:
        fig = go.Figure()
        fig.add_annotation(text="No chart data available", showarrow=False)
        return fig

    # Create subplots with custom row heights
    fig = make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.04,
        row_heights=[0.60, 0.18, 0.22],
        subplot_titles=(f"{symbol} Price Action & Trade Levels", "Volume", "MACD (12, 26, 9)"),
    )

    # 1. Candlestick
    fig.add_trace(
        go.Candlestick(
            x=df.index,
            open=df["Open"],
            high=df["High"],
            low=df["Low"],
            close=df["Close"],
            name="Price",
            increasing_line_color="#26a69a",
            decreasing_line_color="#ef5350",
            increasing_fillcolor="#26a69a",
            decreasing_fillcolor="#ef5350",
        ),
        row=1,
        col=1,
    )

    # 2. Moving Averages
    if "EMA_20" in df.columns:
        fig.add_trace(
            go.Scatter(
                x=df.index,
                y=df["EMA_20"],
                name="EMA 20",
                line=dict(color="#29b6f6", width=1.5),
            ),
            row=1,
            col=1,
        )

    if "EMA_50" in df.columns:
        fig.add_trace(
            go.Scatter(
                x=df.index,
                y=df["EMA_50"],
                name="EMA 50",
                line=dict(color="#ffa726", width=1.5),
            ),
            row=1,
            col=1,
        )

    # 3. Trade Level Overlays (if signal data provided)
    if signal_data:
        sl = signal_data.get("stop_loss")
        tp1 = signal_data.get("tp1")
        tp2 = signal_data.get("tp2")
        tp3 = signal_data.get("tp3")
        bz_low = signal_data.get("buy_zone_low")
        bz_high = signal_data.get("buy_zone_high")
        entry = signal_data.get("price")

        # Shaded Buy Zone
        if bz_low and bz_high and bz_low < bz_high:
            fig.add_hrect(
                y0=bz_low,
                y1=bz_high,
                fillcolor="rgba(76, 175, 80, 0.15)",
                layer="below",
                line_width=1,
                line_color="rgba(76, 175, 80, 0.4)",
                annotation_text=" Buy Zone",
                annotation_position="top left",
                annotation_font=dict(size=10, color="#81c784"),
                row=1,
                col=1,
            )

        # Stop Loss Line (Red dashed)
        if sl:
            fig.add_hline(
                y=sl,
                line_dash="dash",
                line_color="#e53935",
                line_width=1.8,
                annotation_text=f"SL: {sl:.2f}",
                annotation_position="bottom right",
                annotation_font=dict(color="#ef5350", size=11),
                row=1,
                col=1,
            )

        # Entry Line (Blue dotted)
        if entry:
            fig.add_hline(
                y=entry,
                line_dash="dot",
                line_color="#42a5f5",
                line_width=1.2,
                annotation_text=f"Entry: {entry:.2f}",
                annotation_position="top right",
                annotation_font=dict(color="#42a5f5", size=11),
                row=1,
                col=1,
            )

        # TP Targets (Green lines)
        if tp1:
            fig.add_hline(
                y=tp1,
                line_dash="dash",
                line_color="#43a047",
                line_width=1.5,
                annotation_text=f"TP1: {tp1:.2f}",
                annotation_position="top right",
                annotation_font=dict(color="#66bb6a", size=11),
                row=1,
                col=1,
            )
        if tp2:
            fig.add_hline(
                y=tp2,
                line_dash="dash",
                line_color="#2e7d32",
                line_width=1.2,
                annotation_text=f"TP2: {tp2:.2f}",
                annotation_position="top right",
                annotation_font=dict(color="#81c784", size=10),
                row=1,
                col=1,
            )

    # 4. Volume Subplot
    vol_colors = ["#26a69a" if c >= o else "#ef5350" for c, o in zip(df["Close"], df["Open"])]
    fig.add_trace(
        go.Bar(
            x=df.index,
            y=df["Volume"],
            name="Volume",
            marker_color=vol_colors,
            opacity=0.7,
        ),
        row=2,
        col=1,
    )

    if "Vol_MA20" in df.columns:
        fig.add_trace(
            go.Scatter(
                x=df.index,
                y=df["Vol_MA20"],
                name="Vol 20MA",
                line=dict(color="#ab47bc", width=1),
            ),
            row=2,
            col=1,
        )

    # 5. MACD Subplot
    if "MACD" in df.columns and "MACD_Signal" in df.columns:
        fig.add_trace(
            go.Scatter(
                x=df.index,
                y=df["MACD"],
                name="MACD",
                line=dict(color="#42a5f5", width=1.5),
            ),
            row=3,
            col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=df.index,
                y=df["MACD_Signal"],
                name="Signal",
                line=dict(color="#ff7043", width=1.5),
            ),
            row=3,
            col=1,
        )

        hist_colors = ["#26a69a" if h >= 0 else "#ef5350" for h in df["MACD_Hist"]]
        fig.add_trace(
            go.Bar(
                x=df.index,
                y=df["MACD_Hist"],
                name="Histogram",
                marker_color=hist_colors,
                opacity=0.6,
            ),
            row=3,
            col=1,
        )

    # Layout styling
    fig.update_layout(
        template="plotly_dark",
        height=680,
        margin=dict(l=40, r=40, t=40, b=20),
        xaxis_rangeslider_visible=False,
        showlegend=False,
        hovermode="x unified",
        paper_bgcolor="#111722",
        plot_bgcolor="#161f30",
    )

    fig.update_xaxes(showgrid=True, gridwidth=0.5, gridcolor="#1e293b")
    fig.update_yaxes(showgrid=True, gridwidth=0.5, gridcolor="#1e293b")

    return fig
