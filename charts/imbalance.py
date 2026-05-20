import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from config import (
    C_DEFICIT, C_SURPLUS, C_GRID, C_MUTED, C_TEXT, C_BG,
    THRESHOLD, BAR_W_MS,
    _base_layout, _now_marker,
)


def parse_imbalance(imbal: pd.DataFrame) -> pd.DataFrame:
    df = imbal.copy().dropna(subset=["odchylka_MWh"])
    df["signal"] = df["odchylka_MWh"].apply(
        lambda v: "DISCHARGE" if v < -THRESHOLD else ("CHARGE" if v > THRESHOLD else "STANDBY")
    )
    df["power_pct"] = df["odchylka_MWh"].apply(
        lambda v: min(100, int(abs(v) / 150 * 100)) if abs(v) > THRESHOLD else 0
    )
    return df


def fig_ceps_dashboard(data: dict) -> go.Figure:
    """7-panelovy CEPS real-time dashboard."""
    df_imbal = data["imbal"]
    df_svr   = data["svr"]
    df_load  = data["load"]
    df_gen   = data["gen"]
    df_res   = data["res"]
    df_freq  = data["freq"]
    df_cb    = data["cb"]
    df_cena  = data["cena"]
    now      = data["now"]

    xrange  = [now.normalize().isoformat(), now.isoformat()]
    now_iso = now.isoformat()

    fig = make_subplots(
        rows=7, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.025,
        row_heights=[0.18, 0.10, 0.12, 0.14, 0.16, 0.14, 0.16],
        subplot_titles=[
            "Systemova odchylka (MW)",
            "Cena odchylky (CZK/MWh)",
            "Zatizeni (MW)",
            "Aktivace SVR - aFRR / mFRR (MW)",
            "Vyroba podle zdroje (MW, 15min)",
            "Preshranicni toky CR (MW) - kladne = export",
            "OZE real-time - Vitr + Solar (MW)",
        ]
    )

    # Panel 1: Odchylka
    if not df_imbal.empty:
        col = df_imbal.columns[0]
        surplus = df_imbal[col] >= 0
        fig.add_trace(go.Bar(
            x=df_imbal.index[surplus], y=df_imbal.loc[surplus, col],
            name="Surplus", marker_color=C_SURPLUS, opacity=0.85,
            hovertemplate="%{x|%H:%M}  %{y:+.1f} MW<extra>Surplus</extra>",
        ), row=1, col=1)
        fig.add_trace(go.Bar(
            x=df_imbal.index[~surplus], y=df_imbal.loc[~surplus, col],
            name="Deficit", marker_color=C_DEFICIT, opacity=0.85,
            hovertemplate="%{x|%H:%M}  %{y:+.1f} MW<extra>Deficit</extra>",
        ), row=1, col=1)
        if len(df_imbal) >= 5:
            ma = df_imbal[col].rolling(5, min_periods=1).mean()
            fig.add_trace(go.Scatter(
                x=df_imbal.index, y=ma, mode="lines", name="5min MA",
                line=dict(color="#212121", width=1.5),
            ), row=1, col=1)
        fig.add_hline(y=0, line_color="#9E9E9E", line_width=0.8, row=1, col=1)

    # Panel 2: Cena odchylky
    if not df_cena.empty:
        fig.add_trace(go.Scatter(
            x=df_cena.index, y=df_cena.iloc[:, 0],
            name="Cena odchylky", mode="lines+markers",
            line=dict(color="#7B1FA2", width=2, shape="hv"),
            fill="tozeroy", fillcolor="rgba(123,31,162,0.08)",
            hovertemplate="%{x|%H:%M}  %{y:,.0f} CZK/MWh<extra>Cena</extra>",
        ), row=2, col=1)

    # Panel 3: Zatizeni
    if not df_load.empty:
        if "Load [MW]" in df_load.columns:
            fig.add_trace(go.Scatter(
                x=df_load.index, y=df_load["Load [MW]"],
                name="Zatizeni", mode="lines",
                line=dict(color="#E91E63", width=1.5),
                hovertemplate="%{x|%H:%M}  %{y:,.0f} MW<extra>Zatizeni</extra>",
            ), row=3, col=1)
        if "Load including pumping [MW]" in df_load.columns:
            fig.add_trace(go.Scatter(
                x=df_load.index, y=df_load["Load including pumping [MW]"],
                name="Zatizeni vcetne cerpani", mode="lines",
                line=dict(color="#F48FB1", width=1, dash="dot"),
                hovertemplate="%{x|%H:%M}  %{y:,.0f} MW<extra>vcetne cerpani</extra>",
            ), row=3, col=1)

    # Panel 4: SVR aktivace
    SVR_CFG = [
        ("aFRR+ [MW]", "#1565C0", "aFRR+"),
        ("aFRR- [MW]", "#C62828", "aFRR-"),
        ("mFRR+ [MW]", "#2E7D32", "mFRR+"),
        ("mFRR- [MW]", "#E65100", "mFRR-"),
        ("mFRR5 [MW]", "#7B1FA2", "mFRR5"),
    ]
    if not df_svr.empty:
        for api_col, color, label in SVR_CFG:
            if api_col in df_svr.columns:
                r2, g2, b2 = int(color[1:3],16), int(color[3:5],16), int(color[5:7],16)
                fig.add_trace(go.Scatter(
                    x=df_svr.index, y=df_svr[api_col],
                    name=label, mode="lines",
                    line=dict(color=color, width=1.2),
                    fill="tozeroy",
                    fillcolor=f"rgba({r2},{g2},{b2},0.25)",
                    hovertemplate=f"{label}: %{{y:.2f}} MW<extra></extra>",
                ), row=4, col=1)
        fig.add_hline(y=0, line_color="#9E9E9E", line_width=0.8, row=4, col=1)

    # Panel 5: Vyroba podle zdroje
    GEN_CFG = [
        ("NPP [MW]",   "#7B1FA2", "Jaderne (NPP)"),
        ("TPP [MW]",   "#5D4037", "Tepelne (TPP)"),
        ("CCGT [MW]",  "#FF7043", "Paroplynove (CCGT)"),
        ("HPP [MW]",   "#1565C0", "Vodni (HPP)"),
        ("PsPP [MW]",  "#006064", "Precerpavaci (PsPP)"),
        ("AltPP [MW]", "#66BB6A", "Alternativni (AltPP)"),
        ("WPP [MW]",   "#29B6F6", "Vitr (WPP)"),
        ("PVPP [MW]",  "#F9A825", "Solar (PVPP)"),
    ]
    if not df_gen.empty:
        for api_col, color, label in GEN_CFG:
            if api_col in df_gen.columns and df_gen[api_col].sum() > 0:
                r2, g2, b2 = int(color[1:3],16), int(color[3:5],16), int(color[5:7],16)
                fig.add_trace(go.Scatter(
                    x=df_gen.index, y=df_gen[api_col],
                    name=label, stackgroup="gen",
                    line=dict(width=0, color=color),
                    fillcolor=f"rgba({r2},{g2},{b2},0.85)",
                    hovertemplate=f"{label}: %{{y:,.0f}} MW<extra></extra>",
                ), row=5, col=1)

    # Panel 6: Preshranicni toky
    CB_CFG = [
        ("PSE Actual [MW]",    "#E53935", "PSE (Polsko)"),
        ("SEPS Actual [MW]",   "#FB8C00", "SEPS (Slovensko)"),
        ("APG Actual [MW]",    "#43A047", "APG (Rakousko)"),
        ("TenneT Actual [MW]", "#1E88E5", "TenneT (DE zapad)"),
        ("50HzT Actual [MW]",  "#8E24AA", "50HzT (DE vychod)"),
    ]
    if not df_cb.empty:
        for api_col, color, label in CB_CFG:
            if api_col in df_cb.columns:
                r2, g2, b2 = int(color[1:3],16), int(color[3:5],16), int(color[5:7],16)
                pos = df_cb[api_col].clip(lower=0)
                neg = df_cb[api_col].clip(upper=0)
                fig.add_trace(go.Scatter(
                    x=df_cb.index, y=pos,
                    name=label, stackgroup="cb_pos",
                    line=dict(width=0, color=color),
                    fillcolor=f"rgba({r2},{g2},{b2},0.7)",
                    hovertemplate=f"{label}: %{{y:+.0f}} MW<extra></extra>",
                ), row=6, col=1)
                fig.add_trace(go.Scatter(
                    x=df_cb.index, y=neg,
                    name=label + " imp", stackgroup="cb_neg",
                    line=dict(width=0, color=color),
                    fillcolor=f"rgba({r2},{g2},{b2},0.4)",
                    showlegend=False,
                    hovertemplate=f"{label}: %{{y:+.0f}} MW<extra></extra>",
                ), row=6, col=1)
        if "Net Export (MW)" in df_cb.columns:
            fig.add_trace(go.Scatter(
                x=df_cb.index, y=df_cb["Net Export (MW)"],
                name="Net Export", mode="lines",
                line=dict(color="#212121", width=2),
                hovertemplate="Net: %{y:+.0f} MW<extra></extra>",
            ), row=6, col=1)
        fig.add_hline(y=0, line_color="#9E9E9E", line_width=0.8, row=6, col=1)

    # Panel 7: OZE real-time
    if not df_res.empty:
        if "WPP [MW]" in df_res.columns:
            fig.add_trace(go.Scatter(
                x=df_res.index, y=df_res["WPP [MW]"],
                name="Vitr RT", stackgroup="oze",
                line=dict(width=0, color="#29B6F6"),
                fillcolor="rgba(41,182,246,0.75)",
                hovertemplate="%{x|%H:%M}  Vitr: %{y:.1f} MW<extra></extra>",
            ), row=7, col=1)
        if "PVPP [MW]" in df_res.columns:
            fig.add_trace(go.Scatter(
                x=df_res.index, y=df_res["PVPP [MW]"],
                name="Solar RT", stackgroup="oze",
                line=dict(width=0, color="#F9A825"),
                fillcolor="rgba(249,168,37,0.75)",
                hovertemplate="%{x|%H:%M}  Solar: %{y:.1f} MW<extra></extra>",
            ), row=7, col=1)
    if not df_freq.empty:
        fig.add_trace(go.Scatter(
            x=df_freq.index, y=df_freq.iloc[:, 0],
            name="Frekvence (Hz)", mode="lines",
            line=dict(color="#00897B", width=1.2),
            hovertemplate="%{x|%H:%M}  %{y:.4f} Hz<extra></extra>",
        ), row=7, col=1)
        fig.add_hrect(y0=49.8, y1=50.2,
                      fillcolor="rgba(0,137,123,0.05)",
                      line_width=0, row=7, col=1)

    for r in range(1, 8):
        fig.add_vline(x=now_iso, line_color=C_SURPLUS,
                      line_width=1.5, line_dash="dot", row=r, col=1)

    fig.update_layout(
        height=1400,
        title_text="CEPS Real-time — " + now.strftime("%d.%m.%Y %H:%M"),
        template="plotly_white",
        hovermode="x unified",
        barmode="relative",
        bargap=0.05,
        showlegend=True,
        legend=dict(
            orientation="h", y=-0.03, x=0,
            font=dict(size=9), bgcolor="rgba(255,255,255,0.85)",
        ),
        margin=dict(l=70, r=30, t=70, b=80),
    )

    tick_cfg = dict(
        type="date",
        tickformat="%H:%M",
        range=xrange,
        gridcolor=C_GRID,
        title_text="Cas",
        title_font=dict(size=10),
        showticklabels=True,
    )
    for r in range(1, 8):
        fig.update_xaxes(**tick_cfg, row=r, col=1)

    y_labels = {
        1: "MW", 2: "CZK/MWh", 3: "MW",
        4: "MW", 5: "MW", 6: "MW", 7: "MW",
    }
    for r, label in y_labels.items():
        fig.update_yaxes(
            title_text=label, gridcolor=C_GRID,
            title_font=dict(size=10), row=r, col=1,
        )

    return fig


def fig_ceps_combined(df_imbal: pd.DataFrame, df_price: pd.DataFrame,
                      load_actual, load_fc, now: pd.Timestamp, height=320):
    fig = make_subplots(specs=[[{"secondary_y": True}]])

    if not df_imbal.empty:
        surplus = df_imbal["odchylka_MW"] >= 0
        fig.add_trace(go.Bar(
            x=df_imbal.index[surplus], y=df_imbal.loc[surplus, "odchylka_MW"],
            name="Surplus", marker_color=C_SURPLUS, opacity=0.8,
            hovertemplate="%{x|%H:%M}  %{y:+.1f} MW<extra>Surplus</extra>",
        ), secondary_y=False)
        fig.add_trace(go.Bar(
            x=df_imbal.index[~surplus], y=df_imbal.loc[~surplus, "odchylka_MW"],
            name="Deficit", marker_color=C_DEFICIT, opacity=0.8,
            hovertemplate="%{x|%H:%M}  %{y:+.1f} MW<extra>Deficit</extra>",
        ), secondary_y=False)
        if len(df_imbal) >= 5:
            ma = df_imbal["odchylka_MW"].rolling(5, min_periods=1).mean()
            fig.add_trace(go.Scatter(
                x=df_imbal.index, y=ma, mode="lines", name="5min MA",
                line=dict(color="#212121", width=1.5), hoverinfo="skip", opacity=0.7,
            ), secondary_y=False)
        fig.add_hline(y=0, line_color="#9E9E9E", line_width=0.8)
        last = df_imbal["odchylka_MW"].iloc[-1]
        fig.add_annotation(
            x=df_imbal.index[-1], y=last,
            text=f"<b>{last:+.1f} MW</b>",
            showarrow=False, yshift=14 if last >= 0 else -14,
            font=dict(size=11, color=C_SURPLUS if last >= 0 else C_DEFICIT),
            bgcolor="rgba(255,255,255,.85)", borderpad=2,
        )

    day_start = now.normalize()
    if load_fc is not None and not load_fc.empty:
        s = load_fc[load_fc.index >= day_start]
        if not s.empty:
            fig.add_trace(go.Scatter(
                x=s.index, y=s.values, mode="lines", name="Prognóza zatížení",
                line=dict(color="#26A69A", width=1.5, dash="dot"),
                hovertemplate="Prognóza: %{y:,.0f} MW<extra></extra>",
            ), secondary_y=True)
    if load_actual is not None and not load_actual.empty:
        s = load_actual[(load_actual.index >= day_start) & (load_actual.index <= now)]
        if not s.empty:
            fig.add_trace(go.Scatter(
                x=s.index, y=s.values, mode="lines", name="Zatížení skutečnost",
                line=dict(color="#E91E63", width=1.5),
                hovertemplate="Zatížení: %{y:,.0f} MW<extra></extra>",
            ), secondary_y=True)

    if not df_price.empty:
        fig.add_trace(go.Scatter(
            x=df_price.index, y=df_price["cena_CZK_MWh"],
            mode="lines+markers", name="Cena odchylky [CZK/MWh]",
            line=dict(color="#7B1FA2", width=2, shape="hv", dash="dash"),
            hovertemplate="%{x|%H:%M}  %{y:,.0f} CZK/MWh<extra></extra>",
            yaxis="y3",
        ))

    _now_marker(fig, now)
    _base_layout(fig, height=height)
    fig.update_layout(
        barmode="relative", bargap=0.05,
        hovermode="x unified",
        xaxis=dict(
            type="date", tickformat="%H:%M\n%d.%m",
            range=[day_start.isoformat(), now.isoformat()],
            gridcolor=C_GRID,
        ),
        yaxis =dict(title_text="MW (odchylka)", gridcolor=C_GRID),
        yaxis2=dict(title_text="MW (zatížení)", overlaying="y",
                    side="right", showgrid=False),
        yaxis3=dict(title_text="CZK/MWh", overlaying="y",
                    side="right", position=0.97, showgrid=False,
                    anchor="free"),
        legend=dict(orientation="h", y=-0.25, x=0, font=dict(size=10)),
    )
    return fig


def fig_ceps_svr(df: pd.DataFrame, now: pd.Timestamp, height=240):
    """Aktivace SVR v ČR z ČEPS — stacked bar [MW]."""
    fig = go.Figure()
    if df.empty:
        return _base_layout(fig, height=height)
    pos_cols = ["aFRR+ [MW]", "mFRR+ [MW]", "mFRR5 [MW]"]
    neg_cols = ["aFRR- [MW]", "mFRR- [MW]"]
    colors = {
        "aFRR+ [MW]": "#1565C0",
        "aFRR- [MW]": "#C62828",
        "mFRR+ [MW]": "#2E7D32",
        "mFRR- [MW]": "#E65100",
        "mFRR5 [MW]": "#7B1FA2",
    }
    for col in pos_cols + neg_cols:
        if col not in df.columns:
            continue
        fig.add_trace(go.Bar(
            x=df.index, y=df[col], name=col,
            marker_color=colors.get(col, "#9E9E9E"),
            hovertemplate=f"{col}: %{{y:.2f}} MW<extra></extra>",
        ))
    fig.add_hline(y=0, line_color="#9E9E9E", line_width=0.8)
    _now_marker(fig, now)
    _base_layout(fig, height=height)
    fig.update_layout(
        barmode="relative", bargap=0.05,
        hovermode="x unified",
        xaxis=dict(type="date", tickformat="%H:%M", gridcolor=C_GRID),
        yaxis=dict(title_text="MW", gridcolor=C_GRID),
    )
    return fig


def fig_ceps_imbalance_price(df: pd.DataFrame, now: pd.Timestamp, height=200):
    """Odhadovaná cena odchylky ČR z ČEPS [CZK/MWh]."""
    fig = go.Figure()
    if df.empty:
        fig.add_annotation(text="Cena odchylky nedostupna",
                           x=0.5, y=0.5, xref="paper", yref="paper",
                           showarrow=False, font=dict(size=12, color=C_MUTED))
    else:
        fig.add_trace(go.Scatter(
            x=df.index, y=df["cena_CZK_MWh"],
            mode="lines+markers", name="Cena odchylky",
            line=dict(color="#7B1FA2", width=2, shape="hv"),
            fill="tozeroy", fillcolor="rgba(123,31,162,0.10)",
            hovertemplate="%{x|%H:%M}  %{y:,.0f} CZK/MWh<extra></extra>",
        ))
        last_val = df["cena_CZK_MWh"].iloc[-1]
        fig.add_annotation(
            x=df.index[-1], y=last_val,
            text=f"<b>{last_val:,.0f}</b>",
            showarrow=False, yshift=14,
            font=dict(size=11, color="#7B1FA2"),
            bgcolor="rgba(255,255,255,.85)", borderpad=2,
        )
    _now_marker(fig, now)
    _base_layout(fig, height=height)
    today = now.normalize()
    fig.update_layout(
        hovermode="x unified",
        xaxis=dict(
            type="date", tickformat="%H:%M",
            range=[today.isoformat(), (today + pd.Timedelta(days=1)).isoformat()],
            gridcolor=C_GRID,
        ),
        yaxis=dict(title_text="CZK/MWh", gridcolor=C_GRID),
    )
    return fig


def fig_imbalance(df, now, load_actual=None, load_fc=None, height=290):
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    if df.empty:
        return _base_layout(fig, height=height)

    surplus = df["odchylka_MWh"] >= 0
    fig.add_trace(go.Bar(x=df.index[surplus], y=df.loc[surplus,"odchylka_MWh"],
                         marker_color=C_SURPLUS, name="Surplus",
                         hovertemplate="<b>%{x|%a %d.%m %H:%M}</b><br>+%{y:.1f} MWh<extra></extra>"),
                  secondary_y=False)
    fig.add_trace(go.Bar(x=df.index[~surplus], y=df.loc[~surplus,"odchylka_MWh"],
                         marker_color=C_DEFICIT, name="Deficit",
                         hovertemplate="<b>%{x|%a %d.%m %H:%M}</b><br>%{y:.1f} MWh<extra></extra>"),
                  secondary_y=False)
    if len(df) >= 4:
        ma = df["odchylka_MWh"].rolling(4, min_periods=1).mean()
        fig.add_trace(go.Scatter(x=df.index, y=ma, mode="lines", name="1h avg",
                                 line=dict(color="#212121", width=1.5), hoverinfo="skip", opacity=.7),
                      secondary_y=False)
    fig.add_hline(y=0, line_color="#9E9E9E", line_width=.8)
    fig.add_hline(y=THRESHOLD,  line_color="#9E9E9E", line_width=.4, line_dash="dot")
    fig.add_hline(y=-THRESHOLD, line_color="#9E9E9E", line_width=.4, line_dash="dot")
    last = df["odchylka_MWh"].iloc[-1]
    fig.add_annotation(x=df.index[-1], y=last, text=f"<b>{last:+.1f}</b>",
                       showarrow=False, yshift=14 if last >= 0 else -14,
                       font=dict(size=12, color=C_SURPLUS if last >= 0 else C_DEFICIT),
                       bgcolor="rgba(255,255,255,.85)", borderpad=2)

    day_start = now.normalize()
    if load_fc is not None and not load_fc.empty:
        s = load_fc[(load_fc.index >= day_start) & (load_fc.index <= now)]
        if not s.empty:
            fig.add_trace(go.Scatter(
                x=s.index, y=s.values, mode="lines", name="Prognóza zatížení",
                line=dict(color="#26A69A", width=1.5, dash="dot"),
                hovertemplate="Prognóza: %{y:,.0f} MW<extra></extra>",
            ), secondary_y=True)
    if load_actual is not None and not load_actual.empty:
        s = load_actual[(load_actual.index >= day_start) & (load_actual.index <= now)]
        if not s.empty:
            fig.add_trace(go.Scatter(
                x=s.index, y=s.values, mode="lines", name="Zatížení skutečnost",
                line=dict(color="#E91E63", width=1.5),
                hovertemplate="Zatížení: %{y:,.0f} MW<extra></extra>",
            ), secondary_y=True)

    _now_marker(fig, now)
    _base_layout(fig, height=height)
    fig.update_layout(barmode="relative", bargap=.15)
    fig.update_layout(xaxis=dict(type="date", tickformat="%H:%M\n%d.%m", gridcolor=C_GRID))
    fig.update_yaxes(title_text="MWh / 15 min", secondary_y=False)
    fig.update_yaxes(title_text="MW", secondary_y=True, showgrid=False)
    return fig


def fig_signal(df_imbal, now, height=110):
    fig = go.Figure()
    if df_imbal.empty:
        return _base_layout(fig, height=height)
    start = now.normalize()
    for sig, color, val in [
        ("DISCHARGE", "#E65100", -1),
        ("CHARGE",    "#2E7D32",  1),
        ("STANDBY",   "#9E9E9E",  0),
    ]:
        mask = df_imbal["signal"] == sig
        if mask.any():
            fig.add_trace(go.Bar(
                x=df_imbal.index[mask], y=[val] * int(mask.sum()),
                name=sig, marker_color=color, width=800_000,
                hovertemplate=f"%{{x|%H:%M}}  {sig}<extra></extra>",
            ))
    _now_marker(fig, now)
    fig.update_layout(
        height=height, template="plotly_white", hovermode="x unified",
        barmode="overlay", showlegend=True,
        legend=dict(orientation="h", y=-0.35, x=0, font=dict(size=10),
                    bgcolor="rgba(0,0,0,0)"),
        margin=dict(l=65, r=15, t=10, b=45),
        xaxis=dict(type="date", tickformat="%H:%M",
                   range=[start.isoformat(), now.isoformat()], gridcolor=C_GRID),
        yaxis=dict(tickvals=[-1, 0, 1], ticktext=["DISCHARGE", "STANDBY", "CHARGE"],
                   gridcolor=C_GRID),
    )
    return fig


def _act_series(df_act, reserve_kw, direction_kw) -> pd.Series:
    for col in df_act.columns:
        col_str = str(col).lower()
        if reserve_kw.lower() in col_str and direction_kw.lower() in col_str:
            s = df_act[col].dropna()
            return s.tz_convert("Europe/Prague") if s.index.tz else s
    return pd.Series(dtype=float)


def fig_activation_prices(df_act, now, height=220):
    fig = go.Figure()
    start = now.normalize()
    if df_act.empty:
        fig.add_annotation(text="Data cen aktivace nejsou dostupná",
                           x=0.5, y=0.5, xref="paper", yref="paper",
                           showarrow=False, font=dict(size=12, color=C_MUTED))
    else:
        for reserve, direction, color in [
            ("aFRR", "Up",   "#1565C0"),
            ("aFRR", "Down", "#C62828"),
            ("mFRR", "Up",   "#2E7D32"),
        ]:
            s = _act_series(df_act, reserve, direction)
            if s.empty:
                continue
            fig.add_trace(go.Scatter(
                x=s.index, y=s.values,
                name=f"{reserve} {direction}", mode="lines",
                line=dict(color=color, width=2),
                hovertemplate=f"{reserve} {direction}: %{{y:,.2f}} EUR/MWh<extra></extra>",
            ))
    _now_marker(fig, now)
    fig.update_layout(
        height=height,
        title_text="Ceny aktivace záložních rezerv [EUR/MWh]",
        template="plotly_white", hovermode="x unified",
        legend=dict(orientation="h", y=-0.22, x=0, font=dict(size=10),
                    bgcolor="rgba(0,0,0,0)"),
        margin=dict(l=65, r=15, t=40, b=55),
        xaxis=dict(type="date", tickformat="%H:%M",
                   range=[start.isoformat(), now.isoformat()], gridcolor=C_GRID),
        yaxis=dict(title_text="EUR/MWh", gridcolor=C_GRID),
    )
    return fig


def balancing_strategy_ema(imbalance, ema_periods, threshold_mw):
    ema    = imbalance.ewm(span=ema_periods, adjust=False).mean()
    signal = pd.Series("STANDBY", index=imbalance.index)
    signal[ema < -threshold_mw] = "DISCHARGE"
    signal[ema >  threshold_mw] = "CHARGE"
    return ema, signal


def balancing_strategy_holt(imbalance, alpha=0.3, beta=0.1, horizon=6, step_minutes=5):
    if imbalance.empty:
        return pd.Series(dtype=float), pd.Series(dtype=float)

    values = imbalance.values
    level = float(values[0])
    trend = 0.0
    smoothed = np.empty(len(values))
    smoothed[0] = level
    for i in range(1, len(values)):
        new_level = alpha * values[i] + (1 - alpha) * (level + trend)
        trend     = beta  * (new_level - level) + (1 - beta) * trend
        level     = new_level
        smoothed[i] = level

    last_idx = imbalance.index[-1]
    future_idx = pd.date_range(
        start=last_idx + pd.Timedelta(minutes=step_minutes),
        periods=horizon, freq=f"{step_minutes}min",
    )
    forecast_vals = [level + (h + 1) * trend for h in range(horizon)]
    return (
        pd.Series(smoothed, index=imbalance.index),
        pd.Series(forecast_vals, index=future_idx),
    )


def fig_balancing_strategy(df_imbal, ema, signal, threshold_mw, now,
                           ema_forecast=None,
                           holt_smoothed=None, holt_forecast=None,
                           height=380):
    start = now.normalize()
    end_x = now + pd.Timedelta(hours=2)
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        vertical_spacing=0.10,
        subplot_titles=["Odchylka + predikce (EMA + Holt's 30min) [MWh/15min]", "Signál"],
        row_heights=[0.65, 0.35],
    )

    surplus = df_imbal["odchylka_MWh"] >= 0
    fig.add_trace(go.Bar(
        x=df_imbal.index[surplus], y=df_imbal.loc[surplus, "odchylka_MWh"],
        marker_color=C_SURPLUS, name="Surplus",
        hovertemplate="+%{y:.1f} MWh<extra>Surplus</extra>",
    ), row=1, col=1)
    fig.add_trace(go.Bar(
        x=df_imbal.index[~surplus], y=df_imbal.loc[~surplus, "odchylka_MWh"],
        marker_color=C_DEFICIT, name="Deficit",
        hovertemplate="%{y:.1f} MWh<extra>Deficit</extra>",
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=ema.index, y=ema.values, mode="lines", name="EMA predikce",
        line=dict(color="#E65100", width=2),
        hovertemplate="EMA: %{y:.1f} MWh<extra></extra>",
    ), row=1, col=1)
    if ema_forecast is not None and not ema_forecast.empty:
        fig.add_trace(go.Scatter(
            x=ema_forecast.index, y=ema_forecast.values, mode="lines",
            name="EMA forecast",
            line=dict(color="#E65100", width=2, dash="dash"),
            hovertemplate="EMA forecast: %{y:.1f} MWh<extra></extra>",
            showlegend=False,
        ), row=1, col=1)
    if holt_smoothed is not None and not holt_smoothed.empty:
        fig.add_trace(go.Scatter(
            x=holt_smoothed.index, y=holt_smoothed.values, mode="lines",
            name="Holt's predikce",
            line=dict(color="#E91E63", width=2),
            hovertemplate="Holt's: %{y:.1f} MWh<extra></extra>",
        ), row=1, col=1)
    if holt_forecast is not None and not holt_forecast.empty:
        fig.add_trace(go.Scatter(
            x=holt_forecast.index, y=holt_forecast.values, mode="lines",
            name="Holt's forecast (30 min)",
            line=dict(color="#E91E63", width=2, dash="dash"),
            hovertemplate="Holt's forecast: %{y:.1f} MWh<extra></extra>",
            showlegend=False,
        ), row=1, col=1)
    for y_val, color in [(threshold_mw, "#2E7D32"), (-threshold_mw, "#E65100")]:
        fig.add_hline(y=y_val, line_color=color, line_dash="dash", line_width=1, row=1, col=1)
    fig.add_vline(x=now.isoformat(), line_color=C_SURPLUS, line_width=1.5)

    sig_vals = {"DISCHARGE": -1, "STANDBY": 0, "CHARGE": 1}
    for action, color in [("DISCHARGE", "#E65100"), ("STANDBY", "#9E9E9E"), ("CHARGE", "#2E7D32")]:
        mask = signal == action
        if not mask.any():
            continue
        fig.add_trace(go.Bar(
            x=signal.index[mask], y=[sig_vals[action]] * int(mask.sum()),
            name=f"{action}", marker_color=color, width=BAR_W_MS,
            hovertemplate=f"{action}: %{{x|%H:%M}}<extra></extra>",
        ), row=2, col=1)

    fig.update_layout(
        height=height, template="plotly_white", hovermode="x unified", barmode="overlay",
        legend=dict(orientation="h", y=-0.12, x=0, font=dict(size=10),
                    bgcolor="rgba(0,0,0,0)"),
        margin=dict(l=65, r=15, t=40, b=60),
        xaxis=dict(type="date", tickformat="%H:%M",
                   range=[start.isoformat(), end_x.isoformat()], gridcolor=C_GRID),
    )
    fig.update_yaxes(title_text="MWh", gridcolor=C_GRID, row=1, col=1)
    fig.update_yaxes(title_text="Signál", gridcolor=C_GRID, row=2, col=1,
                     tickvals=[-1, 0, 1], ticktext=["DISCHARGE", "STANDBY", "CHARGE"])
    return fig
