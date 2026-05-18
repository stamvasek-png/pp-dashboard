import pandas as pd
import folium
import plotly.graph_objects as go
import streamlit as st
from data.entsog import POINTS_CONFIG

def fig_gas_flows_bar(pivot: pd.DataFrame, height: int = 380) -> go.Figure:
    """Sloupcový graf fyzických toků CZ — netto GWh/d."""
    colors = {
        "Brandov/Waidhaus (DE)": "#1565C0",
        "Lanžhot (SK)":          "#2E7D32",
        "Český Těšín (PL)":      "#F57F17",
        "Zásobníky":             "#6A1B9A",
        "Distribuce":            "#C62828",
        "Koneční spotřebitelé":  "#E65100",
    }
    fig = go.Figure()
    for col in pivot.columns:
        if pivot[col].abs().sum() < 0.1:
            continue
        fig.add_trace(go.Bar(
            x=pivot.index, y=pivot[col],
            name=col,
            marker_color=colors.get(col, "#9E9E9E"),
            hovertemplate=f"<b>{col}</b><br>%{{x|%d.%m.%Y}}<br>%{{y:.1f}} GWh/d<extra></extra>",
        ))
    fig.add_hline(y=0, line_color="black", line_width=0.8)
    fig.update_layout(
        barmode="relative", height=height, template="plotly_white",
        hovermode="x unified",
        title="Fyzické toky plynu CZ — netto (+ import, − export) [GWh/d]",
        legend=dict(orientation="h", y=-0.15),
        xaxis=dict(tickformat="%d.%m", gridcolor="#f0f0f0"),
        yaxis=dict(title="GWh/d"),
        margin=dict(l=60, r=20, t=50, b=80),
    )
    return fig


def fig_gas_point_history(pivot: pd.DataFrame, point: str, height: int = 260) -> go.Figure:
    """Historický graf pro jeden hraniční přechod."""
    fig = go.Figure()
    if point not in pivot.columns:
        return fig
    series = pivot[point]
    color  = "#1565C0" if series.iloc[-1] >= 0 else "#C62828"
    fig.add_trace(go.Scatter(
        x=series.index, y=series.values, mode="lines", name=point,
        line=dict(color=color, width=2),
        fill="tozeroy", fillcolor=color.replace(")", ",0.1)").replace("rgb","rgba"),
        hovertemplate="%{x|%d.%m.%Y}  %{y:.1f} GWh/d<extra></extra>",
    ))
    fig.add_hline(y=0, line_color="black", line_width=0.8)
    fig.update_layout(
        height=height, template="plotly_white", hovermode="x unified",
        title=f"Historický tok — {point}",
        xaxis=dict(tickformat="%d.%m", gridcolor="#f0f0f0"),
        yaxis=dict(title="GWh/d  (+ import, − export)"),
        margin=dict(l=60, r=20, t=50, b=40),
    )
    return fig


def build_gas_map(pivot: pd.DataFrame) -> str:
    """Interaktivní mapa fyzických toků CZ."""
    if pivot.empty or len(pivot) < 2:
        return folium.Map(location=[49.8, 15.5], zoom_start=7, tiles="CartoDB positron")._repr_html_()

    last      = pivot.iloc[-2]
    prev      = pivot.iloc[-3]
    dod       = last - prev
    dod_pct   = (dod / prev.abs().replace(0, float("nan")) * 100).fillna(0)
    date_label = pivot.index[-2].strftime("%d.%m.%Y")

    m = folium.Map(location=[49.8, 15.5], zoom_start=7, tiles="CartoDB positron")

    for name, cfg in POINTS_CONFIG.items():
        val   = float(last.get(name, 0) or 0)
        delta = float(dod.get(name, 0) or 0)
        dpct  = float(dod_pct.get(name, 0) or 0)
        sign  = "+" if val >= 0 else ""
        dsign = "+" if delta >= 0 else ""

        if name == "Zásobníky":
            color      = "#6A1B9A" if val < 0 else "#43A047"
            flow_label = f"vtláčení {abs(val):.1f} GWh/d" if val < 0 else f"těžba {abs(val):.1f} GWh/d"
        elif val > 0:
            color      = "#1565C0"
            flow_label = f"import do CZ: {val:.1f} GWh/d"
        elif val < 0:
            color      = "#C62828"
            flow_label = f"export z CZ: {abs(val):.1f} GWh/d"
        else:
            color      = "#9E9E9E"
            flow_label = "bez toku"

        if abs(dpct) < 1:   dod_str = "beze změny"
        elif dpct > 0:      dod_str = f"▲ +{dpct:.1f}% vs předchozí den"
        else:               dod_str = f"▼ {dpct:.1f}% vs předchozí den"

        radius  = 6 if val == 0 else max(8, min(28, abs(val) * 0.08))
        opacity = 0.4 if val == 0 else 0.75

        popup_html = (
            f"<div style='font-family:sans-serif;min-width:190px'>"
            f"<b style='font-size:13px'>{cfg['flag']} {name}</b><br>"
            f"<span style='color:#888;font-size:10px'>Fyzický tok (ENTSO-G)</span>"
            f"<hr style='margin:4px 0'>"
            f"<b>{flow_label}</b><br>"
            f"<span style='color:{color}'>{dod_str}</span><br>"
            f"<span style='color:#888;font-size:10px'>Delta: {dsign}{delta:.1f} GWh/d</span><br>"
            f"<span style='color:#888;font-size:10px'>Datum dat: {date_label}</span>"
            f"</div>"
        )

        folium.CircleMarker(
            location=[cfg["lat"], cfg["lon"]],
            radius=radius, color=color, weight=2,
            fill=True, fill_color=color, fill_opacity=opacity,
            popup=folium.Popup(popup_html, max_width=240),
            tooltip=f"{cfg['flag']} {name}: {sign}{val:.0f} GWh/d",
        ).add_to(m)

        folium.Marker(
            location=[cfg["lat"] + 0.13, cfg["lon"] + 0.05],
            icon=folium.DivIcon(
                html=(
                    f'<div style="font-size:11px;font-weight:bold;color:#333;'
                    f'white-space:nowrap;text-shadow:1px 1px 2px white">{name}</div>'
                    f'<div style="font-size:13px;font-weight:bold;color:{color};'
                    f'white-space:nowrap;text-shadow:1px 1px 2px white">'
                    f'{sign}{val:.0f} GWh/d</div>'
                    f'<div style="font-size:10px;color:#666;white-space:nowrap">'
                    f'{dod_str[:25]}</div>'
                ),
                icon_size=(200, 48), icon_anchor=(0, 0),
            ),
        ).add_to(m)

    legend_html = f"""
    <div style="position:fixed;bottom:30px;left:30px;z-index:1000;
         background:white;padding:12px 16px;border-radius:8px;
         box-shadow:2px 2px 8px rgba(0,0,0,0.25);font-size:11px;line-height:1.8">
      <b style="font-size:12px">Fyzické toky CZ (GWh/d)</b><br>
      <span style="color:#1565C0">●</span> Import do CZ<br>
      <span style="color:#C62828">●</span> Export z CZ<br>
      <span style="color:#43A047">●</span> Zásobník — těžba<br>
      <span style="color:#6A1B9A">●</span> Zásobník — vtláčení<br>
      <span style="color:#9E9E9E">●</span> Bez toku (0)<br>
      <hr style="margin:4px 0">
      <span style="font-size:10px;color:#444">📅 Toky (ENTSO-G): <b>{date_label}</b></span><br>
      <i style="font-size:9px;color:#888">Klikni na bod pro detail</i>
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))
    return m._repr_html_()
