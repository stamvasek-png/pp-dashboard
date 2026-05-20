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


def fig_gas_map(df_history: pd.DataFrame, height: int = 800) -> go.Figure:
    """
    Plotly Scattergeo — CEE physical gas flow map.

    Inspired by GasConnect Austria daily CEE Flowchart.
    Shows CZ border crossings with directional arrows,
    broader EU pipeline corridors, country nodes with flags,
    and storage placeholders.
    """
    from data.entsog import _short_name

    # ── Country centroids ─────────────────────────────────────────
    COUNTRY_COORDS = {
        "Czechia":     (49.80, 15.50),
        "Germany":     (51.00, 10.50),
        "Slovakia":    (48.70, 19.50),
        "Poland":      (51.90, 19.50),
        "Austria":     (47.50, 14.00),
        "Hungary":     (47.20, 19.50),
        "Netherlands": (52.30,  5.30),
        "France":      (46.60,  2.50),
        "Belgium":     (50.85,  4.50),
        "Italy":       (43.50, 12.00),
        "Denmark":     (56.00,  9.50),
        "Norway":      (62.00,  9.00),
        "Romania":     (45.80, 24.97),
        "Bulgaria":    (42.73, 25.49),
        "Ukraine":     (49.00, 32.00),
        "Slovenia":    (46.15, 14.99),
        "Croatia":     (45.10, 15.20),
        "Switzerland": (46.80,  8.23),
    }
    COUNTRY_FLAGS = {
        "Czechia": "🇨🇿", "Germany": "🇩🇪", "Slovakia": "🇸🇰",
        "Poland": "🇵🇱", "Austria": "🇦🇹", "Hungary": "🇭🇺",
        "Netherlands": "🇳🇱", "France": "🇫🇷", "Belgium": "🇧🇪",
        "Italy": "🇮🇹", "Denmark": "🇩🇰", "Norway": "🇳🇴",
        "Romania": "🇷🇴", "Bulgaria": "🇧🇬", "Ukraine": "🇺🇦",
        "Slovenia": "🇸🇮", "Croatia": "🇭🇷", "Switzerland": "🇨🇭",
    }

    # ── CZ border crossing points (lat, lon) ─────────────────────
    CZ_BORDER_PTS = {
        "Brandov/Waidhaus (DE)": (50.61, 13.39),
        "Lanžhot (SK)":          (48.72, 17.04),
        "Český Těšín (PL)":      (49.75, 18.62),
    }

    # ── Major CEE border crossings (static reference corridors) ──
    # Each: (name, from_lat, from_lon, to_lat, to_lon, label_offset_lat, label_offset_lon)
    EU_CORRIDORS = [
        # North Sea → DE
        ("Emden/Dornum",     54.80,  7.00, 53.20,  9.50, 0.40, -0.3),
        ("Mallnow (PL→DE)",  52.45, 14.50, 52.45, 13.00, 0.35, 0.0),
        # NL → DE
        ("Oude Statenzijl",  53.20,  7.20, 53.00,  8.80, 0.30, 0.0),
        # BE → DE
        ("Eynatten",         50.70,  6.08, 50.70,  7.20, 0.30, 0.0),
        # DE → AT (Oberkappel)
        ("Oberkappel",       48.50, 13.70, 48.20, 13.70, -0.30, -0.8),
        # AT hub — Baumgarten
        ("Baumgarten",       48.10, 16.90, 48.10, 16.90, 0.0, 0.0),
        # AT → IT (Arnoldstein/Tarvisio)
        ("Arnoldstein",      46.55, 13.70, 46.40, 13.20, -0.35, 0.0),
        # AT → HU (Mosonmagyaróvár)
        ("Mosonmagyaróvár",  47.87, 17.27, 47.50, 18.50, -0.30, 0.5),
        # SK → UA (Veľké Kapušany)
        ("Veľké Kapušany",   48.68, 22.08, 48.68, 23.50, 0.30, 0.0),
        # IT → SI (Gorizia)
        ("Gorizia",          45.95, 13.63, 46.05, 14.30, -0.30, 0.0),
        # HU → RO (Csanádpalota)
        ("Csanádpalota",     46.25, 20.73, 45.80, 22.50, -0.30, 0.5),
    ]

    # ── Storage sites (placeholder — orange dots) ────────────────
    STORAGE_SITES = [
        ("Dolní Bojanovice",  48.85, 17.10, "CZ"),
        ("Háje",              49.10, 14.50, "CZ"),
        ("Štramberk",         49.60, 18.10, "CZ"),
        ("Haidach",           47.95, 13.10, "AT"),
        ("Rehden",            52.60,  8.50, "DE"),
        ("Incukalns",         57.10, 24.70, "LV"),
    ]

    DOMESTIC = {
        "Storage", "Distribution", "Final Consumers",
        "Production", "LNG Terminals",
    }

    CZ_LAT, CZ_LON = COUNTRY_COORDS["Czechia"]
    fig = go.Figure()

    # ── Empty guard ───────────────────────────────────────────────
    if df_history.empty:
        fig.add_annotation(
            text="Žádná data", x=0.5, y=0.5,
            xref="paper", yref="paper", showarrow=False,
        )
        _geo_layout(fig, height, "N/A")
        return fig

    # ── Data prep ─────────────────────────────────────────────────
    df = df_history.copy()
    df["date"] = pd.to_datetime(df["date"], utc=True)
    df["value_GWh"] = pd.to_numeric(
        df.get("value_GWh", 0), errors="coerce"
    ).fillna(0)

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
        dod_eu = net_eu_last.subtract(
            net_eu_prev.reindex(net_eu_last.index, fill_value=0)
        )
        dod_cz = net_cz_last.subtract(
            net_cz_prev.reindex(net_cz_last.index, fill_value=0)
        )
    else:
        dod_eu = pd.Series(0.0, index=net_eu_last.index)
        dod_cz = pd.Series(0.0, index=net_cz_last.index)

    date_label = last_date.strftime("%d.%m.%Y") if pd.notna(last_date) else "N/A"

    # ══════════════════════════════════════════════════════════════
    #  1) EU corridor reference lines (thin, dashed, grey)
    # ══════════════════════════════════════════════════════════════
    for name, f_lat, f_lon, t_lat, t_lon, *_ in EU_CORRIDORS:
        fig.add_trace(go.Scattergeo(
            lat=[f_lat, t_lat], lon=[f_lon, t_lon],
            mode="lines",
            line=dict(width=1.2, color="#B0BEC5", dash="dot"),
            opacity=0.5,
            showlegend=False,
            hoverinfo="skip",
        ))
        # corridor label at midpoint
        mid_lat = (f_lat + t_lat) / 2
        mid_lon = (f_lon + t_lon) / 2
        fig.add_trace(go.Scattergeo(
            lat=[mid_lat], lon=[mid_lon],
            mode="markers+text",
            marker=dict(size=3, color="#78909C", opacity=0.6),
            text=[name],
            textposition="top center",
            textfont=dict(size=7, color="#78909C", family="Arial"),
            showlegend=False,
            hovertemplate=f"<b>{name}</b><br><i>Koridor (referenční)</i><extra></extra>",
        ))

    # ══════════════════════════════════════════════════════════════
    #  2) CZ border crossing arrows (data-driven)
    # ══════════════════════════════════════════════════════════════
    for pt_name, (b_lat, b_lon) in CZ_BORDER_PTS.items():
        val  = float(net_cz_last.get(pt_name, 0.0))
        dval = float(dod_cz.get(pt_name, 0.0))
        cfg  = POINTS_CONFIG.get(pt_name, {})
        flag = cfg.get("flag", "")

        dod_arrow = "▲" if dval > 0 else ("▼" if dval < 0 else "–")
        dod_color = "#2E7D32" if dval > 0 else ("#C62828" if dval < 0 else "#757575")

        # Direction: positive = import into CZ
        if val > 0:
            lat1, lon1, lat2, lon2 = b_lat, b_lon, CZ_LAT, CZ_LON
            color = "#1565C0"
        elif val < 0:
            lat1, lon1, lat2, lon2 = CZ_LAT, CZ_LON, b_lat, b_lon
            color = "#C62828"
        else:
            lat1, lon1, lat2, lon2 = b_lat, b_lon, CZ_LAT, CZ_LON
            color = "#9E9E9E"

        width = max(2.5, min(12, abs(val) * 0.01))

        # Main flow line
        fig.add_trace(go.Scattergeo(
            lat=[lat1, lat2], lon=[lon1, lon2],
            mode="lines",
            line=dict(width=width, color=color),
            opacity=0.80,
            showlegend=False,
            hoverinfo="skip",
        ))

        # Arrowhead at 60 % of line
        a_lat = lat1 * 0.40 + lat2 * 0.60
        a_lon = lon1 * 0.40 + lon2 * 0.60
        angle = math.degrees(math.atan2(lon2 - lon1, lat2 - lat1))
        fig.add_trace(go.Scattergeo(
            lat=[a_lat], lon=[a_lon],
            mode="markers",
            marker=dict(
                symbol="triangle-up",
                size=max(10, int(width * 2.2)),
                color=color,
                angle=angle,
                opacity=0.95,
                line=dict(width=0.5, color="white"),
            ),
            showlegend=False,
            hovertemplate=(
                f"<b>{flag} {pt_name}</b><br>"
                f"Tok: <b>{val:+.1f} GWh/d</b><br>"
                f"DoD: {dval:+.1f} GWh/d {dod_arrow}<extra></extra>"
            ),
        ))

        # Flow label near border point: volume + DoD
        label_text = f"<b>{abs(val):.0f}</b> GWh/d {dod_arrow}{abs(dval):.0f}"
        fig.add_trace(go.Scattergeo(
            lat=[b_lat + 0.35], lon=[b_lon + 0.3],
            mode="text",
            text=[f"{abs(val):.0f} {dod_arrow}{abs(dval):.0f}"],
            textfont=dict(size=10, color=color, family="Arial Black"),
            showlegend=False,
            hoverinfo="skip",
        ))

        # Border point dot
        fig.add_trace(go.Scattergeo(
            lat=[b_lat], lon=[b_lon],
            mode="markers",
            marker=dict(
                size=6, color=color, opacity=0.9,
                symbol="circle",
                line=dict(width=1.5, color="white"),
            ),
            showlegend=False,
            hovertemplate=(
                f"<b>{pt_name}</b><br>"
                f"Lat: {b_lat:.2f}  Lon: {b_lon:.2f}<extra></extra>"
            ),
        ))

    # ══════════════════════════════════════════════════════════════
    #  3) Storage nodes (orange)
    # ══════════════════════════════════════════════════════════════
    stor_val  = float(net_cz_last.get("Zásobníky", 0.0))
    stor_dval = float(dod_cz.get("Zásobníky", 0.0))
    stor_arrow = "▲" if stor_dval > 0 else ("▼" if stor_dval < 0 else "–")
    stor_label = "vtláčení" if stor_val < 0 else "těžba"

    for s_name, s_lat, s_lon, s_country in STORAGE_SITES:
        is_cz = s_country == "CZ"
        s_size = 10 if is_cz else 7
        s_opacity = 0.90 if is_cz else 0.55
        s_text = f"{abs(stor_val):.0f}" if is_cz else ""
        s_hover = (
            f"<b>🟠 {s_name}</b><br>"
            f"{stor_label}: <b>{abs(stor_val):.1f} GWh/d</b><br>"
            f"DoD: {stor_dval:+.1f} GWh/d {stor_arrow}<extra></extra>"
        ) if is_cz else (
            f"<b>🟠 {s_name}</b><br>"
            f"<i>Data GIE — placeholder</i><extra></extra>"
        )

        fig.add_trace(go.Scattergeo(
            lat=[s_lat], lon=[s_lon],
            mode="markers+text" if s_text else "markers",
            marker=dict(
                symbol="diamond",
                size=s_size,
                color="#FF8F00",
                opacity=s_opacity,
                line=dict(width=1.5, color="#E65100"),
            ),
            text=[s_text] if s_text else None,
            textposition="top right",
            textfont=dict(size=8, color="#E65100") if s_text else None,
            showlegend=False,
            hovertemplate=s_hover,
        ))

    # ══════════════════════════════════════════════════════════════
    #  4) Country nodes — flags + net flow
    # ══════════════════════════════════════════════════════════════
    for country, (c_lat, c_lon) in COUNTRY_COORDS.items():
        val  = float(net_eu_last.get(country, 0.0))
        dval = float(dod_eu.get(country, 0.0))
        flag = COUNTRY_FLAGS.get(country, "")
        is_cz = country == "Czechia"

        dod_arrow = "▲" if dval > 0 else ("▼" if dval < 0 else "–")

        if abs(val) < 0.5:
            color, sz = "#9E9E9E", 10
        elif val > 0:
            color = "#1565C0"
            sz = max(10, min(28, abs(val) * 0.02))
        else:
            color = "#C62828"
            sz = max(10, min(28, abs(val) * 0.02))

        if is_cz:
            sz = max(sz, 18)

        fig.add_trace(go.Scattergeo(
            lat=[c_lat], lon=[c_lon],
            mode="markers+text",
            marker=dict(
                size=sz,
                color=color,
                opacity=0.85 if is_cz else 0.70,
                line=dict(
                    width=3.0 if is_cz else 1.5,
                    color="#FF8F00" if is_cz else "white",
                ),
            ),
            text=[f"{flag} {country}"],
            textposition="bottom center",
            textfont=dict(
                size=11 if is_cz else 9,
                color="#212121" if is_cz else "#616161",
                family="Arial",
            ),
            showlegend=False,
            hovertemplate=(
                f"<b>{flag} {country}</b><br>"
                f"Net: <b>{val:+.1f} GWh/d</b><br>"
                f"DoD: {dval:+.1f} GWh/d {dod_arrow}<br>"
                f"<i>+ netto import, − netto export</i>"
                f"<extra></extra>"
            ),
        ))

        # Value label above country dot
        if abs(val) >= 0.5:
            fig.add_trace(go.Scattergeo(
                lat=[c_lat + 0.55], lon=[c_lon],
                mode="text",
                text=[f"{val:+.0f} {dod_arrow}{abs(dval):.0f}"],
                textfont=dict(
                    size=9 if is_cz else 8,
                    color=color,
                    family="Arial Black",
                ),
                showlegend=False,
                hoverinfo="skip",
            ))

    _geo_layout(fig, height, date_label)
    return fig


def _geo_layout(fig: go.Figure, height: int, date_label: str) -> None:
    """Map layout — clean Europe, country outlines only."""
    fig.update_layout(
        margin=dict(l=0, r=0, t=50, b=0),
        title=dict(
            text=(
                f"<b>Fyzické toky plynu CEE</b>"
                f"<br><span style='font-size:11px;color:#757575'>"
                f"Gasday: {date_label} · 06:00–06:00 CET · Hodnoty v GWh/d"
                f" · Zdroj: ENTSO-G TP</span>"
            ),
            font=dict(size=15, color="#212121"),
            x=0.01,
        ),
        showlegend=False,
        geo=dict(
            scope="europe",
            resolution=50,
            projection_type="mercator",
            showland=True,       landcolor="#FAFAFA",
            showocean=True,      oceancolor="#E8F0FE",
            showlakes=False,
            showrivers=False,
            showcountries=True,  countrycolor="#BDBDBD",
            countrywidth=0.9,
            showsubunits=False,
            showcoastlines=True, coastlinecolor="#BDBDBD",
            coastlinewidth=0.7,
            showframe=False,
            center=dict(lat=49.5, lon=14.0),
            projection_scale=4.0,
            lonaxis=dict(range=[-5, 35]),
            lataxis=dict(range=[42, 58]),
        ),
        height=height,
        autosize=True,
    )
