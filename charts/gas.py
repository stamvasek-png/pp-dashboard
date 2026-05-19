import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from data.entsog import POINTS_CONFIG

FLOW_COLORS = [
    "#1565C0","#C62828","#2E7D32","#F57F17","#6A1B9A",
    "#00838F","#E65100","#4527A0","#558B2F","#AD1457",
]

GAS_NODES = {
    "Brandov/Waidhaus (DE)": (50.61, 13.39),
    "Lanžhot (SK)":          (48.72, 17.04),
    "Český Těšín (PL)":      (49.75, 18.62),
    "Zásobníky":             (49.75, 15.80),
    "Distribuce":            (49.40, 16.20),
    "Koneční spotřebitelé":  (49.10, 15.50),
    "CZ":  (49.80, 15.50),
    "DE":  (51.50, 10.00),
    "SK":  (48.70, 19.50),
    "PL":  (52.20, 20.00),
    "AT":  (47.80, 13.50),
    "HU":  (47.20, 19.00),
    "NL":  (52.20,  5.30),
    "FR":  (46.50,  2.50),
}

# Koridory: (uzel_od, uzel_do, label, datový_klíč_v_pivot)
# Každý segment = jedna čára; data_key=None → šedá bez šipky
GAS_CORRIDORS = [
    ("DE",  "Brandov/Waidhaus (DE)", "DE→Brandov",        "Brandov/Waidhaus (DE)"),
    ("Brandov/Waidhaus (DE)", "CZ",  "Brandov→CZ",        "Brandov/Waidhaus (DE)"),
    ("CZ",  "Lanžhot (SK)",          "CZ→Lanžhot",        "Lanžhot (SK)"),
    ("CZ",  "Český Těšín (PL)",      "CZ→Těšín",          "Český Těšín (PL)"),
    ("CZ",  "Zásobníky",             "CZ→Zásobníky",      "Zásobníky"),
    ("NL",  "DE",   "NL→DE Transit",  None),
    ("FR",  "DE",   "FR→DE",          None),
    ("DE",  "AT",   "DE→AT",          None),
    ("AT",  "SK",   "AT→SK Transit",  None),
    ("SK",  "HU",   "SK→HU",          None),
    ("PL",  "Český Těšín (PL)", "PL→Těšín", None),
]


def fig_flow_timeseries(
    df: pd.DataFrame,
    countries: list,
    points: list,
    systems: list,
    directions: list,
    chart_type: str = "Linie",
    height: int = 380,
) -> go.Figure:
    """
    Časová osa fyzických toků.
    df má sloupce: date, countryLabel, pointsNames,
                   adjacentSystemsKey, directionKey, value_GWh
    """
    fig = go.Figure()

    if any([countries, points, systems, directions]):
        mask = pd.Series(True, index=df.index)
        if countries:
            mask &= df["countryLabel"].isin(countries)
        if points:
            mask &= df["pointsNames"].isin(points)
        if systems:
            mask &= df["adjacentSystemsKey"].isin(systems)
        if directions:
            mask &= df["directionKey"].isin(directions)
        filtered = df[mask].copy()
    else:
        filtered = df.copy()

    if filtered.empty:
        fig.add_annotation(
            text="Žádná data pro vybranou kombinaci filtrů",
            x=0.5, y=0.5, xref="paper", yref="paper",
            showarrow=False, font=dict(size=14, color="#888"),
        )
        return fig

    group_cols = ["countryLabel", "pointsNames"]
    groups = filtered.groupby(group_cols)

    for i, (key, grp) in enumerate(groups):
        series = grp.groupby("date")["value_GWh"].sum().sort_index()
        label  = f"{key[0]} · {key[1]}"
        color  = FLOW_COLORS[i % len(FLOW_COLORS)]
        if chart_type == "Sloupcový":
            fig.add_trace(go.Bar(
                x=series.index, y=series.values, name=label,
                marker_color=color,
                hovertemplate=f"<b>{label}</b><br>%{{x|%d.%m.%Y}}<br>%{{y:.1f}} GWh/d<extra></extra>",
            ))
        elif chart_type == "Plocha":
            fig.add_trace(go.Scatter(
                x=series.index, y=series.values, mode="lines", name=label,
                line=dict(color=color, width=1.8),
                fill="tozeroy",
                fillcolor="rgba({},{},{},0.2)".format(
                    int(color[1:3],16), int(color[3:5],16), int(color[5:7],16)
                ),
                hovertemplate=f"<b>{label}</b><br>%{{x|%d.%m.%Y}}<br>%{{y:.1f}} GWh/d<extra></extra>",
            ))
        else:
            fig.add_trace(go.Scatter(
                x=series.index, y=series.values, mode="lines", name=label,
                line=dict(color=color, width=1.8),
                hovertemplate=f"<b>{label}</b><br>%{{x|%d.%m.%Y}}<br>%{{y:.1f}} GWh/d<extra></extra>",
            ))

    fig.add_hline(y=0, line_color="black", line_width=0.8)
    if chart_type == "Sloupcový":
        fig.update_layout(barmode="relative")
    fig.update_layout(
        height=height,
        title="Fyzické toky — časová osa [GWh/d]",
        template="plotly_white",
        hovermode="x unified",
        legend=dict(orientation="h", y=-0.2),
        xaxis=dict(tickformat="%d.%m.%Y", gridcolor="#f0f0f0"),
        yaxis=dict(title="GWh/d", gridcolor="#f0f0f0"),
        margin=dict(l=60, r=20, t=50, b=80),
    )
    return fig


def fig_flow_seasonality(
    df: pd.DataFrame,
    countries: list,
    points: list,
    systems: list,
    directions: list,
    years: list,
    chart_type: str = "Linie",
    height: int = 360,
) -> go.Figure:
    """
    Sezonnost — agregát vybraných filtrů, jedna křivka = jeden rok.
    Osa X = den v roce (1–366).
    """
    YEAR_COLORS = {
        2020: "#BDBDBD", 2021: "#90A4AE", 2022: "#42A5F5",
        2023: "#1565C0", 2024: "#F57F17", 2025: "#C62828",
        2026: "#2E7D32",
    }

    fig = go.Figure()

    if any([countries, points, systems, directions]):
        mask = pd.Series(True, index=df.index)
        if countries:
            mask &= df["countryLabel"].isin(countries)
        if points:
            mask &= df["pointsNames"].isin(points)
        if systems:
            mask &= df["adjacentSystemsKey"].isin(systems)
        if directions:
            mask &= df["directionKey"].isin(directions)
        filtered = df[mask].copy()
    else:
        filtered = df.copy()

    if filtered.empty:
        fig.add_annotation(
            text="Žádná data pro vybranou kombinaci filtrů",
            x=0.5, y=0.5, xref="paper", yref="paper",
            showarrow=False, font=dict(size=14, color="#888"),
        )
        return fig

    filtered["date"]        = pd.to_datetime(filtered["date"])
    filtered["year"]        = filtered["date"].dt.year
    filtered["day_of_year"] = filtered["date"].dt.day_of_year

    sel_years = years if years else sorted(filtered["year"].unique())

    for yr in sorted(sel_years):
        grp = filtered[filtered["year"] == yr]
        if grp.empty:
            continue
        series = grp.groupby("day_of_year")["value_GWh"].sum().sort_index()
        color  = YEAR_COLORS.get(yr, "#9E9E9E")
        width  = 2.5 if yr == pd.Timestamp.now().year else 1.5
        if chart_type == "Sloupcový":
            fig.add_trace(go.Bar(
                x=series.index, y=series.values, name=str(yr),
                marker_color=color,
                hovertemplate=f"<b>{yr}</b><br>Den %{{x}}<br>%{{y:.1f}} GWh/d<extra></extra>",
            ))
        elif chart_type == "Plocha":
            fig.add_trace(go.Scatter(
                x=series.index, y=series.values, mode="lines", name=str(yr),
                line=dict(color=color, width=width),
                fill="tozeroy",
                fillcolor="rgba({},{},{},0.2)".format(
                    int(color[1:3],16), int(color[3:5],16), int(color[5:7],16)
                ),
                hovertemplate=f"<b>{yr}</b><br>Den %{{x}}<br>%{{y:.1f}} GWh/d<extra></extra>",
            ))
        else:
            fig.add_trace(go.Scatter(
                x=series.index, y=series.values, mode="lines", name=str(yr),
                line=dict(color=color, width=width),
                hovertemplate=f"<b>{yr}</b><br>Den %{{x}}<br>%{{y:.1f}} GWh/d<extra></extra>",
            ))

    fig.add_hline(y=0, line_color="black", line_width=0.8)
    if chart_type == "Sloupcový":
        fig.update_layout(barmode="group")
    fig.update_layout(
        height=height,
        title="Sezonnost fyzických toků [GWh/d]",
        template="plotly_white",
        hovermode="x unified",
        legend=dict(orientation="h", y=-0.2),
        xaxis=dict(
            title="Den v roce",
            tickvals=[1,32,60,91,121,152,182,213,244,274,305,335],
            ticktext=["Led","Úno","Bře","Dub","Kvě","Čvn",
                      "Čvc","Srp","Zář","Říj","Lis","Pro"],
            gridcolor="#f0f0f0",
        ),
        yaxis=dict(title="GWh/d", gridcolor="#f0f0f0"),
        margin=dict(l=60, r=20, t=50, b=80),
    )
    return fig

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
    """Interaktivní mapa fyzických toků CZ — čáry s šipkami."""
    import folium
    import numpy as np

    m = folium.Map(
        location=[49.5, 11.0],
        zoom_start=5,
        tiles="CartoDB positron",
    )

    if not pivot.empty and len(pivot) >= 2:
        last       = pivot.iloc[-2]
        prev       = pivot.iloc[-3]
        dod        = last - prev
        dod_pct    = (dod / prev.abs().replace(0, float("nan")) * 100).fillna(0)
        date_label = pivot.index[-2].strftime("%d.%m.%Y")
    else:
        last = prev = dod = dod_pct = {}
        date_label = "N/A"

    def get_val(key):
        if key is None: return 0.0
        val = last.get(key, 0)
        if hasattr(val, "iloc"):
            val = val.iloc[0] if len(val) > 0 else 0
        try:
            return float(val or 0)
        except (TypeError, ValueError):
            return 0.0

    def arrow_marker(m, p1, p2, color, pos=0.65):
        lat = p1[0] * (1 - pos) + p2[0] * pos
        lon = p1[1] * (1 - pos) + p2[1] * pos
        angle = np.degrees(np.arctan2(p2[1] - p1[1], p2[0] - p1[0]))
        folium.Marker(
            [lat, lon],
            icon=folium.DivIcon(
                html=(
                    f'<div style="font-size:16px;color:{color};'
                    f'transform:rotate({-angle:.0f}deg);'
                    f'text-shadow:1px 1px 2px white;'
                    f'line-height:1">&#10148;</div>'
                ),
                icon_size=(20, 20),
                icon_anchor=(10, 10),
            ),
        ).add_to(m)

    # Kresli koridory
    for from_key, to_key, label, data_key in GAS_CORRIDORS:
        p1 = GAS_NODES.get(from_key)
        p2 = GAS_NODES.get(to_key)
        if not p1 or not p2:
            continue

        if data_key is None:
            # Koridor bez dat — šedá tenká čára, bez šipky
            folium.PolyLine(
                locations=[p1, p2],
                color="#BDBDBD", weight=1.5, opacity=0.3,
                tooltip=label,
            ).add_to(m)
            continue

        val = get_val(data_key)

        if val < 0:
            draw_p1, draw_p2 = p2, p1
            color = "#C62828"
        elif val > 0:
            draw_p1, draw_p2 = p1, p2
            color = "#1565C0"
        else:
            draw_p1, draw_p2 = p1, p2
            color = "#BDBDBD"

        weight  = max(1.5, min(10, abs(val) * 0.04))
        opacity = 0.85

        folium.PolyLine(
            locations=[draw_p1, draw_p2],
            color=color, weight=weight, opacity=opacity,
            tooltip=f"{label}: {val:+.1f} GWh/d",
        ).add_to(m)
        arrow_marker(m, draw_p1, draw_p2, color)

    # CZ centroid
    folium.CircleMarker(
        location=GAS_NODES["CZ"],
        radius=10,
        color="#333", weight=2,
        fill=True, fill_color="#FF8F00",
        fill_opacity=0.9,
        tooltip="CZ — síťový uzel",
    ).add_to(m)

    # Anotace datových bodů
    for name, cfg in POINTS_CONFIG.items():
        coords = GAS_NODES.get(name)
        if not coords:
            continue
        val   = float(last.get(name, 0) or 0)
        delta = float(dod.get(name, 0) if hasattr(dod, "get") else 0)
        dpct  = float(dod_pct.get(name, 0) if hasattr(dod_pct, "get") else 0)
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
        elif dpct > 0:      dod_str = f"▲ +{dpct:.1f}%"
        else:               dod_str = f"▼ {dpct:.1f}%"

        folium.CircleMarker(
            location=coords,
            radius=7,
            color=color, weight=2,
            fill=True, fill_color=color, fill_opacity=0.9,
            popup=folium.Popup(
                f"<div style='font-family:sans-serif;min-width:190px'>"
                f"<b style='font-size:13px'>{cfg['flag']} {name}</b><br>"
                f"<span style='color:#888;font-size:10px'>Fyzický tok (ENTSO-G)</span>"
                f"<hr style='margin:4px 0'>"
                f"<b>{flow_label}</b><br>"
                f"<span style='color:{color}'>{dod_str} vs předchozí den</span><br>"
                f"<span style='color:#888;font-size:10px'>"
                f"Delta: {dsign}{delta:.1f} GWh/d · Datum: {date_label}</span>"
                f"</div>",
                max_width=240,
            ),
            tooltip=f"{cfg['flag']} {name}: {sign}{val:.0f} GWh/d",
        ).add_to(m)

        folium.Marker(
            location=[coords[0] + 0.15, coords[1] + 0.05],
            icon=folium.DivIcon(
                html=(
                    f'<div style="font-size:11px;font-weight:bold;'
                    f'color:#333;white-space:nowrap;'
                    f'text-shadow:1px 1px 2px white">'
                    f'{cfg["flag"]} {name}</div>'
                    f'<div style="font-size:13px;font-weight:bold;'
                    f'color:{color};white-space:nowrap;'
                    f'text-shadow:1px 1px 2px white">'
                    f'{sign}{val:.0f} GWh/d</div>'
                    f'<div style="font-size:10px;color:#666;'
                    f'white-space:nowrap">{dod_str}</div>'
                ),
                icon_size=(200, 50),
                icon_anchor=(0, 0),
            ),
        ).add_to(m)

    legend_html = f"""
    <div style="position:fixed;bottom:30px;left:30px;z-index:1000;
         background:white;padding:12px 16px;border-radius:8px;
         box-shadow:2px 2px 8px rgba(0,0,0,0.25);
         font-size:11px;line-height:1.8">
      <b style="font-size:12px">Fyzické toky plynu [GWh/d]</b><br>
      <span style="color:#1565C0">&#9473;&#9473;&#10148;</span> Import do CZ<br>
      <span style="color:#C62828">&#9473;&#9473;&#10148;</span> Export z CZ<br>
      <span style="color:#43A047">&#9679;</span> Zásobník — těžba<br>
      <span style="color:#6A1B9A">&#9679;</span> Zásobník — vtláčení<br>
      <span style="color:#BDBDBD">&#9473;&#9473;&#10148;</span> Koridor bez dat<br>
      <hr style="margin:4px 0">
      <span style="font-size:10px;color:#444">
        &#128197; Data: <b>{date_label}</b> (ENTSO-G)</span><br>
      <i style="font-size:9px;color:#888">
        Tloušťka čáry = objem · Klikni pro detail</i>
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))
    return m._repr_html_()
