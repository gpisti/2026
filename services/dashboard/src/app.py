import requests
import pandas as pd
import streamlit as st
import plotly.express as px

API_URL = "http://web_api:8000/api/momentum/latest"

st.set_page_config(
    page_title="Választás 2026 – Média Momentum Radar",
    page_icon="📡",
    layout="wide",
)


@st.cache_data(ttl=120)
def load_data():
    try:
        resp = requests.get(API_URL, timeout=10)
        resp.raise_for_status()
        return resp.json().get("data", [])
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Fejléc
# ---------------------------------------------------------------------------
st.title("📡 Választás 2026 – Média Momentum Radar")
st.caption(
    "Valós idejű médiafelügyelet · Frissítés: 2 percenként · "
    "Forrás: 7 magyar hírportál RSS feedje"
)

with st.spinner("Adatok betöltése..."):
    data = load_data()

if data is None:
    st.warning(
        "Nem sikerült elérni az API-t (web_api:8000). "
        "Ellenőrizd, hogy a web_api konténer fut-e, majd frissítsd az oldalt."
    )
    st.stop()

if not data:
    st.info("Jelenleg nincsenek elérhető adatok a vizualizációhoz.")
    st.stop()

df = pd.DataFrame(data)
df["date"] = pd.to_datetime(df["date"])

# ---------------------------------------------------------------------------
# Szűrők a sidebarban
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("🎛️ Szűrők")
    all_entities = sorted(df["entity_name"].unique().tolist())
    selected = st.multiselect(
        "Entitások",
        options=all_entities,
        default=all_entities,
    )
    date_range = st.date_input(
        "Időintervallum",
        value=(df["date"].min().date(), df["date"].max().date()),
    )

mask = df["entity_name"].isin(selected)
if len(date_range) == 2:
    mask &= (df["date"].dt.date >= date_range[0]) & (df["date"].dt.date <= date_range[1])
df_f = df[mask]

# ---------------------------------------------------------------------------
# KPI kártyák (legutóbbi nap)
# ---------------------------------------------------------------------------
st.subheader("📊 Legutóbbi nap – összesített mutatók")
latest = df_f[df_f["date"] == df_f["date"].max()]

if not latest.empty:
    cols = st.columns(len(latest))
    for col, (_, row) in zip(cols, latest.iterrows()):
        adj = row.get("adjusted_momentum")
        delta_val = f"{adj:+.2f}" if adj is not None and pd.notna(adj) else "–"
        col.metric(
            label=row["entity_name"],
            value=f"{row['total_reach']:,}",
            delta=f"Adj. momentum: {delta_val}",
        )

st.divider()

# ---------------------------------------------------------------------------
# 1. Adjusted Momentum vonaldiagram
# ---------------------------------------------------------------------------
st.subheader("🎯 Adjusted Momentum – valós, bias-kiszűrt politikai médiahatás")
st.caption(
    "Képlet: `SUM(reach × (sentiment − portál_baseline))` — "
    "a portálok politikai beállítottságát kiszűri, csak a szokásostól való eltérést méri."
)

fig_adj = px.line(
    df_f,
    x="date",
    y="adjusted_momentum",
    color="entity_name",
    markers=True,
    labels={
        "date": "Dátum",
        "adjusted_momentum": "Adjusted Momentum",
        "entity_name": "Entitás",
    },
    color_discrete_sequence=px.colors.qualitative.Vivid,
)
fig_adj.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.4)
fig_adj.update_traces(line_width=2.5)
fig_adj.update_layout(
    plot_bgcolor="rgba(0,0,0,0)",
    paper_bgcolor="rgba(0,0,0,0)",
    legend_title="Entitás",
    hovermode="x unified",
    yaxis_title="Adjusted Momentum",
    xaxis_title="",
)
st.plotly_chart(fig_adj, use_container_width=True)

st.divider()

# ---------------------------------------------------------------------------
# 2. Nyers átlag-szentiment vonaldiagram
# ---------------------------------------------------------------------------
st.subheader("🧭 Napi átlag-szentiment (nyers, bias nélkül szűrve)")
st.caption(
    "Tartomány: −1.0 (nagyon negatív) → +1.0 (nagyon pozitív). "
    "A portálok alapszintű beállítottságát NEM szűri ki."
)

fig_sent = px.line(
    df_f,
    x="date",
    y="average_sentiment",
    color="entity_name",
    markers=True,
    labels={
        "date": "Dátum",
        "average_sentiment": "Átlag szentiment",
        "entity_name": "Entitás",
    },
    color_discrete_sequence=px.colors.qualitative.Vivid,
)
fig_sent.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.4)
fig_sent.update_layout(
    plot_bgcolor="rgba(0,0,0,0)",
    paper_bgcolor="rgba(0,0,0,0)",
    yaxis_range=[-1.1, 1.1],
    hovermode="x unified",
    yaxis_title="Átlag szentiment",
    xaxis_title="",
)
st.plotly_chart(fig_sent, use_container_width=True)

st.divider()

# ---------------------------------------------------------------------------
# 3. Total Reach oszlopdiagram
# ---------------------------------------------------------------------------
st.subheader("📣 Napi médiaelérés (Total Reach) – Facebook + Reddit összesített")
st.caption("Mennyi közösségi interakció kapcsolódik az entitásról szóló cikkekhez naponta.")

fig_reach = px.bar(
    df_f,
    x="date",
    y="total_reach",
    color="entity_name",
    barmode="group",
    labels={
        "date": "Dátum",
        "total_reach": "Total Reach",
        "entity_name": "Entitás",
    },
    color_discrete_sequence=px.colors.qualitative.Vivid,
)
fig_reach.update_layout(
    plot_bgcolor="rgba(0,0,0,0)",
    paper_bgcolor="rgba(0,0,0,0)",
    hovermode="x unified",
    yaxis_title="Interakciók száma",
    xaxis_title="",
)
st.plotly_chart(fig_reach, use_container_width=True)

st.divider()

# ---------------------------------------------------------------------------
# 4. Nyers adattábla
# ---------------------------------------------------------------------------
with st.expander("🗃️ Nyers adatok megtekintése"):
    display_cols = [
        "date", "entity_name", "total_articles", "total_reach",
        "average_sentiment", "baseline_sentiment", "momentum_score", "adjusted_momentum",
    ]
    st.dataframe(
        df_f[display_cols].sort_values(["date", "entity_name"], ascending=[False, True]),
        use_container_width=True,
        hide_index=True,
    )

# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------
st.caption(
    "🤖 Powered by NYTK/sentiment-ohb3-xlm-roberta-hungarian · "
    "7 RSS forrás · Frissítés: 10 percenként"
)
