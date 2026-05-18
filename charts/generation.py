import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from config import (
    C_SURPLUS, C_GRID, C_MUTED, C_TEXT, C_BG,
    GEN_STACK_ORDER, psr_lookup,
    _base_layout, _now_marker,
)


def fig_generation_area(df_gen, now, height=320):
    fig = go.Figure()
    if df_gen.empty:
        return _base_layout(fig, height=height)
    cols = list(df_gen.columns)

    def _key(c):
        psr = str(c[0]) if isinstance(c, tuple) else str(c)
        return GEN_STACK_ORDER.index(psr) if psr in GEN_STACK_ORDER else 999

    for col in sorted(cols, key=_key):
        name, color = psr_lookup(col)
        series = df_gen[col].fillna(0)
        if series.sum() < 1:
            continue
        if color.startswith("#") and len(color) == 7:
            r = int(color[1:3], 16)
            g = int(color[3:5], 16)
            b = int(color[5:7], 16)
            fill_color = f"rgba({r},{g},{b},0.78)"
        else:
            fill_color = color
        fig.add_trace(go.Scatter(
            x=series.index, y=series.values, stackgroup="gen", name=name,
            line=dict(width=0, color=color), fillcolor=fill_color,
            hovertemplate=f"{name}: %{{y:.0f}} MW<extra></extra>",
        ))
    _now_marker(fig, now)
    _base_layout(fig, height=height)
    fig.update_xaxes(tickformat="%H:%M\n%d.%m")
    fig.update_yaxes(title_text="MW")
    fig.update_layout(hovermode="x unified")
    return fig


def fig_generation_donut(df_gen, height=280):
    fig = go.Figure()
    if df_gen.empty:
        return _base_layout(fig, height=height)
    last = df_gen.dropna(how="all").tail(1)
    if last.empty:
        return _base_layout(fig, height=height)
    items = []
    for col, val in last.iloc[0].items():
        name, color = psr_lookup(col)
        if pd.notna(val) and float(val) > 0:
            items.append((float(val), name, color))
    items.sort(reverse=True)
    total = sum(v for v,_,_ in items) or 1
    fig.add_trace(go.Pie(
        labels=[n for _,n,_ in items], values=[v for v,_,_ in items],
        hole=0.68,
        marker=dict(colors=[c for _,_,c in items], line=dict(color="#fff", width=2)),
        textinfo="none",
        hovertemplate="<b>%{label}</b><br>%{value:.0f} MW · %{percent}<extra></extra>",
        sort=False, direction="clockwise",
    ))
    from config import C_TEXT, C_MUTED
    fig.update_layout(
        height=height, margin=dict(l=5, r=5, t=10, b=10),
        paper_bgcolor=C_BG, plot_bgcolor=C_BG, font=dict(color=C_TEXT, size=10),
        annotations=[dict(
            text=(f"<b style='font-size:22px;color:{C_TEXT}'>{total:,.0f}</b>"
                  f"<br><span style='font-size:9px;color:{C_MUTED};letter-spacing:1px'>MW CELKEM</span>"),
            x=0.5, y=0.5, showarrow=False,
        )],
        showlegend=False,
    )
    return fig


def fig_wind_solar_forecast(ws, now, gen_raw=None, height=240):
    fig = go.Figure()
    start_fc = now.normalize()
    end_fc   = start_fc + pd.Timedelta(days=2)

    if not ws.empty:
        ws_slice = ws[(ws.index >= start_fc) & (ws.index < end_fc)]
        for col in ws_slice.columns:
            psr = str(col[0]) if isinstance(col, tuple) else str(col)
            if psr in ("B16", "Solar"):
                series = ws_slice[col].fillna(0)
                fig.add_trace(go.Scatter(
                    x=series.index, y=series.values, stackgroup="solar", name="Solární prognóza",
                    line=dict(width=0, color="#F9A825"), fillcolor="rgba(249,168,37,0.6)",
                    hovertemplate="Solární prognóza: %{y:.0f} MW<extra></extra>",
                ))

    if gen_raw is not None and not gen_raw.empty:
        wind_col = next(
            (c for c in gen_raw.columns
             if (str(c[0]) if isinstance(c, tuple) else str(c)) == "B19"),
            None,
        )
        if wind_col is not None:
            wind = gen_raw[wind_col].dropna()
            wind = wind[wind.index <= now]
            if not wind.empty:
                fig.add_trace(go.Scatter(
                    x=wind.index, y=wind.values, mode="lines",
                    name="Vítr onshore (skutečnost)",
                    line=dict(color="#29B6F6", width=2, shape="hv"),
                    hovertemplate="Vítr onshore: %{y:.0f} MW<extra></extra>",
                ))

    if fig.data:
        _now_marker(fig, now)
    _base_layout(fig, height=height)
    fig.update_yaxes(title_text="MW")
    fig.update_layout(
        hovermode="x unified",
        xaxis=dict(
            type="date",
            tickformat="%a %d.%m\n%H:%M",
            range=[start_fc.isoformat(), end_fc.isoformat()],
            gridcolor="#f0f0f0",
        ),
    )
    return fig


def render_mix_legend(df_gen) -> str:
    if df_gen.empty:
        return "<div class='mix-legend'><em style='color:#888'>—</em></div>"
    last = df_gen.dropna(how="all").tail(1)
    if last.empty:
        return "<div class='mix-legend'><em style='color:#888'>—</em></div>"
    items = []
    for col, val in last.iloc[0].items():
        name, color = psr_lookup(col)
        if pd.notna(val) and float(val) > 0:
            items.append((float(val), name, color))
    items.sort(reverse=True)
    total = sum(v for v,_,_ in items) or 1
    rows = [
        f'<div class="mix-row">'
        f'<span class="mix-dot" style="background:{c}"></span>'
        f'<span class="mix-name">{n}</span>'
        f'<span class="mix-val">{v:,.0f} MW</span>'
        f'<span class="mix-pct">{v/total*100:.0f}%</span></div>'
        for v, n, c in items
    ]
    return f'<div class="mix-legend">{"".join(rows)}</div>'


def fig_load(load_fc, ceps_load, ceps_gen, now, height=280):
    fig = go.Figure()
    if not load_fc.empty:
        fig.add_trace(go.Scatter(
            x=load_fc.index, y=load_fc.values, mode="lines",
            name="Prognóza zatížení D+1", line=dict(color="#26A69A", width=2, shape="hv"),
            hovertemplate="Prognóza D+1: %{y:,.0f} MW<extra></extra>",
        ))
    if ceps_load is not None and not ceps_load.empty:
        # ČEPS Load columns: "Load [MW]", "Load including pumping [MW]"
        # Czech equivalents: "Zatížení [MW]", "Zatížení vč. čerpání [MW]"
        # Prefer pumping variant as it includes čerpání přečerpávacích elektráren
        PREFERRED = ("Zatížení vč. čerpání [MW]", "Load including pumping [MW]",
                     "Zatížení [MW]", "Load [MW]")
        load_col = next((c for c in PREFERRED if c in ceps_load.columns), None)
        if load_col is None:
            load_col = next(
                (c for c in ceps_load.columns
                 if "load" in str(c).lower() or "zatížení" in str(c).lower()),
                None,
            )
        if load_col is None:
            num_cols = ceps_load.select_dtypes(include="number").columns
            load_col = num_cols[0] if len(num_cols) > 0 else None
        if load_col is not None:
            s = ceps_load[load_col].dropna()
            fig.add_trace(go.Scatter(
                x=s.index, y=s.values, mode="lines",
                name="Zatížení skutečnost (ČEPS)", line=dict(color="#E91E63", width=2, shape="hv"),
                hovertemplate="Zatížení ČEPS: %{y:,.0f} MW<extra></extra>",
            ))
    if ceps_gen is not None and not ceps_gen.empty:
        total_gen = ceps_gen.sum(axis=1).dropna()
        fig.add_trace(go.Bar(
            x=total_gen.index, y=total_gen.values,
            name="Plán výroby (ČEPS)", marker_color="#1565C0", opacity=0.4, yaxis="y",
        ))
    _now_marker(fig, now)
    _base_layout(fig, height=height)
    fig.update_xaxes(tickformat="%H:%M\n%d.%m")
    fig.update_yaxes(title_text="Zatížení / Výroba (MW)")
    fig.update_layout(hovermode="x unified", barmode="overlay")
    return fig


def fig_deltagreen(df1, df2, height=560):
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.1,
                        subplot_titles=("Výkon portfolia [kW]", "Disponibilní flexibilita [kW]"))
    for col, name, color in [
        ("batteryPowerKW",      "Baterie",      "#4A90D9"),
        ("consumptionPowerKW",  "Spotřeba",     "#E8744A"),
        ("photovoltaicPowerKW", "Fotovoltaika", "#F5C518"),
        ("gridPowerKW",         "Síť",          "#2ECC71"),
    ]:
        fig.add_trace(go.Scatter(x=df1["time"], y=df1[col], name=name,
                                 line=dict(color=color, width=1.5),
                                 hovertemplate=f"<b>{name}</b><br>%{{x|%H:%M:%S}}<br>%{{y:.0f}} kW<extra></extra>"),
                      row=1, col=1)
    for col, name, fc, lc in [
        ("upPowerKW",                   "Max UP",         "rgba(0,191,166,.4)",  "#00BFA6"),
        ("downBatteryPowerKW",          "Nabíjení bat.",  "rgba(74,144,217,.4)", "#4A90D9"),
        ("downSolarCurtailmentPowerKW", "Zákaz přetoku",  "rgba(245,197,24,.4)", "#F5C518"),
    ]:
        fig.add_trace(go.Scatter(x=df2["time"], y=df2[col], name=name,
                                 fill="tozeroy", fillcolor=fc, line=dict(color=lc, width=1.5),
                                 hovertemplate=f"<b>{name}</b><br>%{{x|%H:%M:%S}}<br>%{{y:.0f}} kW<extra></extra>"),
                      row=2, col=1)
    last2 = df2.dropna(subset=["upPowerKW","downBatteryPowerKW","downSolarCurtailmentPowerKW"]).iloc[-1]
    total_down = float(last2["downBatteryPowerKW"]) + float(last2["downSolarCurtailmentPowerKW"])
    fig.update_layout(
        height=height,
        title_text=(f"Delta Green Portfolio  |  UP: {round(float(last2['upPowerKW']))} kW  |  "
                    f"DOWN: {round(total_down)} kW"),
        template="plotly_white", hovermode="x unified", showlegend=True,
        legend=dict(orientation="h", y=-0.08, x=0),
        margin=dict(l=60, r=15, t=50, b=40),
    )
    for row in [1, 2]:
        fig.update_xaxes(tickformat="%H:%M", gridcolor=C_GRID, row=row, col=1)
        fig.update_yaxes(title_text="kW", gridcolor=C_GRID, zeroline=True,
                         zerolinecolor="#cccccc", row=row, col=1)
    return fig
