import math
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from data.entsog import POINTS_CONFIG

FLOW_COLORS = [
    "#1565C0","#C62828","#2E7D32","#F57F17","#6A1B9A",
    "#00838F","#E65100","#4527A0","#558B2F","#AD1457",
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


def fig_gas_map(df_history: pd.DataFrame, height: int = 580) -> go.Figure:
    """Plotly Scattergeo — EU gas flows, CZ border crossing arrows."""
    from data.entsog import _short_name

    COUNTRY_COORDS = {
        "Czechia":     (49.80, 15.50),
        "Germany":     (51.50, 10.00),
        "Slovakia":    (48.70, 19.50),
        "Poland":      (52.20, 20.00),
        "Austria":     (47.80, 13.50),
        "Hungary":     (47.20, 19.00),
        "Netherlands": (52.20,  5.30),
        "France":      (46.50,  2.50),
        "Belgium":     (50.50,  4.50),
        "Italy":       (42.50, 12.50),
        "Denmark":     (56.00,  9.50),
        "Norway":      (62.00,  9.00),
        "Romania":     (45.80, 24.97),
        "Bulgaria":    (42.73, 25.49),
        "Ukraine":     (49.00, 32.00),
    }
    COUNTRY_FLAGS = {
        "Czechia": "🇨🇿", "Germany": "🇩🇪", "Slovakia": "🇸🇰",
        "Poland": "🇵🇱", "Austria": "🇦🇹", "Hungary": "🇭🇺",
        "Netherlands": "🇳🇱", "France": "🇫🇷", "Belgium": "🇧🇪",
        "Italy": "🇮🇹", "Denmark": "🇩🇰", "Norway": "🇳🇴",
        "Romania": "🇷🇴", "Bulgaria": "🇧🇬", "Ukraine": "🇺🇦",
    }
    # (border_lat, border_lon)
    CZ_BORDER_PTS = {
        "Brandov/Waidhaus (DE)": (50.61, 13.39),
        "Lanžhot (SK)":          (48.72, 17.04),
        "Český Těšín (PL)":      (49.75, 18.62),
    }
    DOMESTIC = {"Storage", "Distribution", "Final Consumers", "Production", "LNG Terminals"}

    CZ_LAT, CZ_LON = COUNTRY_COORDS["Czechia"]
    fig = go.Figure()

    if df_history.empty:
        fig.add_annotation(text="Žádná data", x=0.5, y=0.5,
                           xref="paper", yref="paper", showarrow=False)
        _geo_layout(fig, height, "N/A")
        return fig

    df = df_history.copy()
    df["date"] = pd.to_datetime(df["date"], utc=True)
    df["value_GWh"] = pd.to_numeric(df.get("value_GWh", 0), errors="coerce").fillna(0)

    last_date = df["date"].max()
    mask_prev = df["date"] < last_date
    prev_date = df.loc[mask_prev, "date"].max() if mask_prev.any() else pd.NaT

    def _net_eu(day):
        sub = df[(df["date"] == day) & ~df["adjacentSystemsKey"].isin(DOMESTIC)]
        e = sub[sub["directionKey"] == "entry"].groupby("countryLabel")["value_GWh"].sum()
        x = sub[sub["directionKey"] == "exit"].groupby("countryLabel")["value_GWh"].sum()
        idx = e.index.union(x.index)
        return e.reindex(idx, fill_value=0) - x.reindex(idx, fill_value=0)

    def _net_cz(day):
        sub = df[(df["date"] == day) & (df["countryLabel"] == "Czechia")].copy()
        sub["pt"] = sub["pointsNames"].apply(_short_name)
        e = sub[sub["directionKey"] == "entry"].groupby("pt")["value_GWh"].sum()
        x = sub[sub["directionKey"] == "exit"].groupby("pt")["value_GWh"].sum()
        idx = e.index.union(x.index)
        return e.reindex(idx, fill_value=0) - x.reindex(idx, fill_value=0)

    net_eu_last = _net_eu(last_date)
    net_cz_last = _net_cz(last_date)

    if pd.notna(prev_date):
        net_eu_prev = _net_eu(prev_date)
        net_cz_prev = _net_cz(prev_date)
        dod_eu = net_eu_last.subtract(net_eu_prev.reindex(net_eu_last.index, fill_value=0))
        dod_cz = net_cz_last.subtract(net_cz_prev.reindex(net_cz_last.index, fill_value=0))
    else:
        dod_eu = pd.Series(0.0, index=net_eu_last.index)
        dod_cz = pd.Series(0.0, index=net_cz_last.index)

    date_label = last_date.strftime("%d.%m.%Y") if pd.notna(last_date) else "N/A"

    # ── CZ border crossing arrows ─────────────────────────────────
    for pt_name, (b_lat, b_lon) in CZ_BORDER_PTS.items():
        val  = float(net_cz_last.get(pt_name, 0.0))
        dval = float(dod_cz.get(pt_name, 0.0))
        cfg  = POINTS_CONFIG.get(pt_name, {})
        flag = cfg.get("flag", "")
        sign  = "+" if val  >= 0 else ""
        dsign = "+" if dval >= 0 else ""

        if val > 0:
            lat1, lon1, lat2, lon2 = b_lat, b_lon, CZ_LAT, CZ_LON
            color = "#1565C0"
        elif val < 0:
            lat1, lon1, lat2, lon2 = CZ_LAT, CZ_LON, b_lat, b_lon
            color = "#C62828"
        else:
            lat1, lon1, lat2, lon2 = b_lat, b_lon, CZ_LAT, CZ_LON
            color = "#9E9E9E"

        width = max(2, min(10, abs(val) * 0.008))

        fig.add_trace(go.Scattergeo(
            lat=[lat1, lat2], lon=[lon1, lon2],
            mode="lines",
            line=dict(width=width, color=color),
            opacity=0.75,
            showlegend=False, hoverinfo="skip",
        ))

        # Arrow head at 65% of line
        a_lat = lat1 * 0.35 + lat2 * 0.65
        a_lon = lon1 * 0.35 + lon2 * 0.65
        angle = math.degrees(math.atan2(lon2 - lon1, lat2 - lat1))
        fig.add_trace(go.Scattergeo(
            lat=[a_lat], lon=[a_lon],
            mode="markers",
            marker=dict(symbol="triangle-up", size=max(8, int(width * 2.5)),
                        color=color, angle=angle, opacity=0.9),
            showlegend=False,
            hovertemplate=(
                f"<b>{flag} {pt_name}</b><br>"
                f"Tok: <b>{sign}{val:.1f} GWh/d</b><br>"
                f"DoD: {dsign}{dval:.1f} GWh/d<extra></extra>"
            ),
        ))

        # Flow label near border point
        fig.add_trace(go.Scattergeo(
            lat=[b_lat + 0.25], lon=[b_lon],
            mode="text",
            text=[f"{sign}{val:.0f}"],
            textfont=dict(size=10, color=color, family="Arial Black"),
            showlegend=False, hoverinfo="skip",
        ))

    # Storage node
    stor_val  = float(net_cz_last.get("Zásobníky", 0.0))
    stor_dval = float(dod_cz.get("Zásobníky", 0.0))
    stor_color = "#6A1B9A" if stor_val < 0 else "#43A047"
    stor_label = "vtláčení" if stor_val < 0 else "těžba"
    stor_sign  = "+" if stor_val >= 0 else ""
    stor_dsign = "+" if stor_dval >= 0 else ""
    fig.add_trace(go.Scattergeo(
        lat=[49.75], lon=[15.80],
        mode="markers+text",
        marker=dict(symbol="square", size=12, color=stor_color, opacity=0.85,
                    line=dict(width=2, color="white")),
        text=[f"🏭{stor_sign}{stor_val:.0f}"],
        textposition="top right",
        textfont=dict(size=9, color=stor_color),
        showlegend=False,
        hovertemplate=(
            f"<b>🏭 Zásobníky</b><br>"
            f"{stor_label}: <b>{abs(stor_val):.1f} GWh/d</b><br>"
            f"DoD: {stor_dsign}{stor_dval:.1f} GWh/d<extra></extra>"
        ),
    ))

    # ── EU country bubbles ────────────────────────────────────────
    for country, (c_lat, c_lon) in COUNTRY_COORDS.items():
        val  = float(net_eu_last.get(country, 0.0))
        dval = float(dod_eu.get(country, 0.0))
        flag = COUNTRY_FLAGS.get(country, "")
        sign  = "+" if val  >= 0 else ""
        dsign = "+" if dval >= 0 else ""
        is_cz = country == "Czechia"

        if abs(val) < 0.5:
            color, size = "#9E9E9E", 8
        elif val > 0:
            color = "#1565C0"
            size  = max(8, min(26, val * 0.015))
        else:
            color = "#C62828"
            size  = max(8, min(26, abs(val) * 0.015))

        fig.add_trace(go.Scattergeo(
            lat=[c_lat], lon=[c_lon],
            mode="markers+text",
            marker=dict(
                size=size,
                color=color,
                opacity=0.8,
                line=dict(width=2.5 if is_cz else 1.5,
                          color="#FF8F00" if is_cz else "white"),
            ),
            text=[f"{flag} {country[:4]}<br>{sign}{val:.0f}"],
            textposition="top center",
            textfont=dict(size=10 if is_cz else 9, color=color),
            showlegend=False,
            hovertemplate=(
                f"<b>{flag} {country}</b><br>"
                f"Net cross-border: <b>{sign}{val:.1f} GWh/d</b><br>"
                f"DoD: {dsign}{dval:.1f} GWh/d<br>"
                f"<i>+ = net importer, − = net exporter</i>"
                f"<extra></extra>"
            ),
        ))

    _geo_layout(fig, height, date_label)
    return fig


def _geo_layout(fig: go.Figure, height: int, date_label: str) -> None:
    fig.update_layout(
        margin=dict(l=0, r=0, t=40, b=0),
        title=dict(
            text=f"Fyzické toky plynu — {date_label} (ENTSO-G)",
            font=dict(size=14),
            x=0.01,
        ),
        showlegend=False,
        geo=dict(
            scope="europe",
            resolution=50,
            showland=True,      landcolor="#F5F5F5",
            showocean=True,     oceancolor="#EAF4FB",
            showlakes=False,
            showrivers=False,
            showcountries=True, countrycolor="#CCCCCC",
            countrywidth=0.8,
            showsubunits=False,
            showcoastlines=True, coastlinecolor="#CCCCCC",
            center=dict(lat=49, lon=13),
            projection_scale=3.5,
            lonaxis=dict(range=[-10, 40]),
            lataxis=dict(range=[35, 65]),
        ),
        height=800,
        autosize=True,
    )

