import numpy as np
import pandas as pd
import networkx as nx
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st
from db_legal import (
    init_db,
    load_real_sc_dataset,
    fetch_connected_cases,
    add_case_and_citation,
    trace_downstream_precedents,
    trace_upstream_authorities,
)

st.set_page_config(
    page_title="LexGraph Judicial Intelligence Studio",
    page_icon="🏛️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# --- OPTION 4 THEME: DEEP OBSIDIAN + CHAMPAGNE GOLD STUDIO ---
st.markdown(
    """
    <style>
    .stApp {
        background: radial-gradient(circle at 80% 10%, #1c1917 0%, #0d1015 48%, #080a0e 100%);
        color: #f5f5f4;
        font-family: 'Inter', 'Segoe UI', Roboto, sans-serif;
    }
    section[data-testid="stSidebar"] {
        background-color: #10131a;
        border-right: 1px solid rgba(217, 160, 54, 0.25);
    }
    .studio-header {
        background: linear-gradient(90deg, rgba(24, 22, 18, 0.96), rgba(14, 17, 23, 0.96));
        border: 1px solid rgba(217, 160, 54, 0.45);
        border-radius: 12px;
        padding: 20px 26px;
        margin-bottom: 18px;
        box-shadow: 0 10px 30px rgba(0, 0, 0, 0.45);
    }
    .studio-title {
        font-size: 1.7rem;
        font-weight: 800;
        letter-spacing: 0.8px;
        color: #f8fafc;
        margin: 0;
    }
    .studio-sub {
        color: #a8a29e;
        font-size: 0.88rem;
        margin-top: 4px;
    }
    .gold-kpi {
        background: linear-gradient(145deg, rgba(34, 29, 20, 0.85), rgba(17, 20, 27, 0.95));
        border: 1px solid rgba(217, 160, 54, 0.55);
        border-radius: 10px;
        padding: 16px 20px;
        box-shadow: 0 8px 20px rgba(0, 0, 0, 0.4);
    }
    .gold-kpi-label {
        font-size: 0.72rem;
        text-transform: uppercase;
        letter-spacing: 1.3px;
        color: #d6d3d1;
        font-weight: 600;
    }
    .gold-kpi-value {
        font-size: 2.0rem;
        font-weight: 800;
        color: #fbbf24;
        margin-top: 4px;
        line-height: 1.15;
    }
    .gold-kpi-badge {
        display: inline-block;
        background: rgba(217, 160, 54, 0.18);
        border: 1px solid rgba(217, 160, 54, 0.45);
        color: #fcd34d;
        font-size: 0.72rem;
        padding: 2px 8px;
        border-radius: 4px;
        margin-top: 6px;
    }
    button[data-baseweb="tab"] {
        font-weight: 600 !important;
        color: #a8a29e !important;
    }
    button[data-baseweb="tab"][aria-selected="true"] {
        color: #fbbf24 !important;
        border-bottom-color: #fbbf24 !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# --- TOP STUDIO BANNER ---
st.markdown(
    """
    <div class="studio-header">
        <div class="studio-title">🏛️ LEXGRAPH JUDICIAL INTELLIGENCE STUDIO</div>
        <div class="studio-sub">High-Performance Case Law Citation Network & Precedent Analyzer • PostgreSQL Recursive CTE Engine</div>
    </div>
    """,
    unsafe_allow_html=True,
)

# --- DATABASE INITIALIZATION ---
try:
    init_db()
    cases = fetch_connected_cases(limit=500)
except Exception as e:
    st.error(f"PostgreSQL Connection Error: {e}")
    st.info("Add `DATABASE_URL = 'postgresql://user:pass@host:5432/dbname?sslmode=require'` in Streamlit Settings → Secrets.")
    st.stop()

# --- LEFT SIDEBAR: SEARCH & QUERY PARAMS ---
with st.sidebar:
    st.markdown("### 🔍 SEARCH & QUERY PARAMS")

    if not cases:
        row_limit = st.slider("Real Judgments to Ingest", 200, 3000, 1000, step=200)
        if st.button("⚖️ Ingest Real Supreme Court CSV", use_container_width=True):
            with st.spinner("Ingesting real Indian Supreme Court judgments into PostgreSQL..."):
                c_cnt, e_cnt = load_real_sc_dataset(max_rows=row_limit)
            st.success(f"Loaded {c_cnt:,} judgments & {e_cnt:,} citation links!")
            st.rerun()
        st.stop()

    df_all = pd.DataFrame(cases)

    keyword_filter = st.text_input("Filter Case Title / Keyword", placeholder="e.g. Union of India, State, Singh...")
    year_range = st.slider("Decision Year Window", 1950, 2026, (1950, 2026))
    direction = st.selectbox(
        "Traversal Direction",
        ["Downstream Cascade (Who Cited This)", "Upstream Lineage (What This Relied On)"],
    )
    max_depth = st.slider("Max Citation Hop Depth", min_value=1, max_value=8, value=6)

    filtered_df = df_all[
        (df_all["decision_year"] >= year_range[0]) & (df_all["decision_year"] <= year_range[1])
    ]
    if keyword_filter.strip():
        mask = filtered_df["case_title"].str.contains(keyword_filter.strip(), case=False, na=False)
        if mask.any():
            filtered_df = filtered_df[mask]

    if filtered_df.empty:
        filtered_df = df_all

    case_options = {
        f"[{r['decision_year']}] {r['case_title'][:48]} (Cited: {r['cited_by_count']})": r["case_id"]
        for _, r in filtered_df.iterrows()
    }
    selected_label = st.selectbox("Anchor Landmark Ruling", list(case_options.keys()))
    selected_case_id = case_options[selected_label]

    st.divider()
    with st.expander("📥 Ingest More Real Judgments"):
        batch_sz = st.slider("CSV Batch Size", 200, 3000, 1200, step=200)
        if st.button("Sync Supreme Court Dataset", use_container_width=True):
            with st.spinner("Syncing real Supreme Court dataset..."):
                c_cnt, e_cnt = load_real_sc_dataset(max_rows=batch_sz)
            st.success(f"Synced {c_cnt:,} cases & {e_cnt:,} edges!")
            st.rerun()

# --- EXECUTE POSTGRESQL WITH RECURSIVE QUERY ---
selected_meta = df_all[df_all["case_id"] == selected_case_id].iloc[0]
if "Downstream" in direction:
    rows = trace_downstream_precedents(selected_case_id, max_depth=int(max_depth))
else:
    rows = trace_upstream_authorities(selected_case_id, max_depth=int(max_depth))

df_tree = pd.DataFrame(rows) if rows else pd.DataFrame()
actual_max_hops = int(df_tree["depth"].max()) if not df_tree.empty else 0
total_citations = int(df_all["cited_by_count"].sum())

# --- 3 STUDIO TABS ---
tab1, tab2, tab3 = st.tabs([
    "🔗 Timeline Cascade (Primary)",
    "📊 Precedent Analytics",
    "⚙️ Database Config & SQL",
])

with tab1:
    k1, k2, k3 = st.columns(3)
    with k1:
        st.markdown(
            f"""
            <div class="gold-kpi">
                <div class="gold-kpi-label">Total Precedent Index</div>
                <div class="gold-kpi-value">{len(df_all):,} Cases</div>
                <div class="gold-kpi-badge">{total_citations:,} Active Citation Edges</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with k2:
        st.markdown(
            f"""
            <div class="gold-kpi">
                <div class="gold-kpi-label">Max Citation Hop Depth</div>
                <div class="gold-kpi-value">{actual_max_hops} Hops</div>
                <div class="gold-kpi-badge">PostgreSQL WITH RECURSIVE</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with k3:
        connected_nodes = len(df_tree) + 1 if not df_tree.empty else 1
        st.markdown(
            f"""
            <div class="gold-kpi">
                <div class="gold-kpi-label">Active Lineage Nodes</div>
                <div class="gold-kpi-value">{connected_nodes:,} Rulings</div>
                <div class="gold-kpi-badge">Status: {selected_meta['status']}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown("<br>", unsafe_allow_html=True)

    st.info(
        f"**Selected Anchor:** {selected_meta['case_title']} (**{selected_meta['decision_year']}**) • "
        f"Bench: *{selected_meta['jurisdiction']}* • Validity: **{selected_meta['status']}**"
    )

    if df_tree.empty:
        st.warning(
            "No recursive edges found in this traversal direction for this case. Try switching **Traversal Direction** in the left sidebar or selecting one of the top-cited rulings."
        )
    else:
        G = nx.DiGraph()
        node_years = {selected_case_id: int(selected_meta["decision_year"])}
        node_depths = {selected_case_id: 0}
        node_titles = {selected_case_id: selected_meta["case_title"]}
        node_statuses = {selected_case_id: selected_meta["status"]}

        for _, r in df_tree.iterrows():
            if "Downstream" in direction:
                u, v = r["parent_case_id"], r["child_case_id"]
                node_years[v] = int(r["child_year"])
                node_depths[v] = int(r["depth"])
                node_titles[v] = r["child_title"]
                node_statuses[v] = r["child_status"]
                node_titles.setdefault(u, r["parent_title"])
            else:
                u, v = r["ancestor_case_id"], r["child_case_id"]
                node_years[u] = int(r["ancestor_year"])
                node_depths[u] = int(r["depth"])
                node_titles[u] = r["ancestor_title"]
                node_statuses[u] = r["ancestor_status"]
                node_titles.setdefault(v, r["child_title"])
            G.add_edge(u, v, treatment=r["treatment"])

        pos = {}
        bucket_counts = {}
        for n in G.nodes():
            yr = node_years.get(n, 2000)
            dp = node_depths.get(n, 0)
            idx = bucket_counts.get((yr, dp), 0)
            bucket_counts[(yr, dp)] = idx + 1
            y_offset = dp + (idx * 0.22)
            pos[n] = (yr, y_offset)

        fig_cascade = go.Figure()

        for u, v in G.edges():
            x0, y0 = pos[u]
            x1, y1 = pos[v]
            xm = (x0 + x1) / 2.0
            ym = (y0 + y1) / 2.0 + 0.35
            t = np.linspace(0, 1, 22)
            bx = (1 - t) ** 2 * x0 + 2 * (1 - t) * t * xm + t ** 2 * x1
            by = (1 - t) ** 2 * y0 + 2 * (1 - t) * t * ym + t ** 2 * y1
            fig_cascade.add_trace(
                go.Scatter(
                    x=bx,
                    y=by,
                    mode="lines",
                    line=dict(width=1.4, color="rgba(251, 191, 36, 0.32)"),
                    hoverinfo="none",
                    showlegend=False,
                )
            )

        status_colors = {
            "GOOD_LAW": "#fbbf24",
            "DISTINGUISHED": "#f97316",
            "OVERRULED": "#ef4444",
        }

        nx_vals, ny_vals, n_labels, n_hover, n_colors, n_sizes = [], [], [], [], [], []
        for n in G.nodes():
            x, y = pos[n]
            nx_vals.append(x)
            ny_vals.append(y)
            yr = node_years.get(n, "")
            title_full = node_titles.get(n, n)
            st_val = node_statuses.get(n, "GOOD_LAW")

            if n == selected_case_id or G.degree(n) >= 2:
                n_labels.append(f"{title_full[:22]} ({yr})")
            else:
                n_labels.append("")

            if n == selected_case_id:
                n_colors.append("#fef08a")
                n_sizes.append(18)
            else:
                n_colors.append(status_colors.get(st_val, "#fbbf24"))
                n_sizes.append(12)

            n_hover.append(
                f"<b>{title_full}</b><br>Decision Year: {yr}<br>Citation Hop: {node_depths.get(n, 0)}<br>Validity: {st_val}"
            )

        fig_cascade.add_trace(
            go.Scatter(
                x=nx_vals,
                y=ny_vals,
                mode="markers+text",
                text=n_labels,
                textposition="top center",
                textfont=dict(size=10, color="#fef3c7"),
                hovertext=n_hover,
                hoverinfo="text",
                marker=dict(
                    size=n_sizes,
                    color=n_colors,
                    line=dict(width=1.5, color="#1c1917"),
                    opacity=0.95,
                ),
                showlegend=False,
            )
        )

        fig_cascade.update_layout(
            title=dict(
                text="Chronological Citation Cascade (X: Decision Year • Y: Recursive Hop Depth)",
                font=dict(size=15, color="#fbbf24"),
            ),
            xaxis=dict(
                title="Decision Year Timeline",
                gridcolor="rgba(217, 160, 54, 0.12)",
                zeroline=False,
            ),
            yaxis=dict(
                title="Citation Hop Depth (0 to Max Hops)",
                gridcolor="rgba(217, 160, 54, 0.12)",
                zeroline=False,
            ),
            paper_bgcolor="rgba(16, 19, 26, 0.9)",
            plot_bgcolor="rgba(10, 12, 17, 0.75)",
            font=dict(color="#e7e5e4"),
            height=460,
            margin=dict(l=20, r=20, t=50, b=25),
        )
        st.plotly_chart(fig_cascade, use_container_width=True)

        st.markdown("#### 📋 Recursive Precedent Lineage Table")
        display_cols = [c for c in df_tree.columns if c != "path_ids"]
        st.dataframe(df_tree[display_cols], use_container_width=True, hide_index=True)

        csv_data = df_tree[display_cols].to_csv(index=False).encode("utf-8")
        st.download_button(
            "⬇️ Download Precedent Lineage CSV",
            data=csv_data,
            file_name=f"lexgraph_lineage_{selected_case_id}.csv",
            mime="text/csv",
        )

with tab2:
    col1, col2 = st.columns(2)
    with col1:
        df_all["decade"] = (df_all["decision_year"] // 10) * 10
        dec_df = df_all.groupby("decade").size().reset_index(name="cases_count")
        fig_dec = px.bar(
            dec_df,
            x="decade",
            y="cases_count",
            title="Supreme Court Judgments Indexed by Decade",
            color_discrete_sequence=["#fbbf24"],
        )
        fig_dec.update_layout(
            paper_bgcolor="rgba(16,19,26,0.9)",
            plot_bgcolor="rgba(10,12,17,0.75)",
            font=dict(color="#f5f5f4"),
            height=360,
        )
        st.plotly_chart(fig_dec, use_container_width=True)

    with col2:
        st_df = df_all["status"].value_counts().reset_index()
        st_df.columns = ["status", "count"]
        fig_donut = px.pie(
            st_df,
            names="status",
            values="count",
            hole=0.55,
            title="Judicial Precedent Validity Breakdown",
            color="status",
            color_discrete_map={
                "GOOD_LAW": "#fbbf24",
                "DISTINGUISHED": "#f97316",
                "OVERRULED": "#ef4444",
            },
        )
        fig_donut.update_layout(
            paper_bgcolor="rgba(16,19,26,0.9)",
            font=dict(color="#f5f5f4"),
            height=360,
        )
        st.plotly_chart(fig_donut, use_container_width=True)

    st.markdown("#### 🏆 Top 15 Most Cited Landmark Rulings in PostgreSQL")
    top15 = df_all[["case_id", "case_title", "decision_year", "cited_by_count", "cites_count", "status"]].head(15)
    st.dataframe(top15, use_container_width=True, hide_index=True)

with tab3:
    col_form, col_sql = st.columns([1, 1.1])

    with col_form:
        st.markdown("#### ✍️ Add New Court Ruling & Citation Edge")
        existing_choices = ["None"] + [
            f"{c['case_id']} — {c['case_title'][:35]} ({c['decision_year']})" for c in cases[:100]
        ]
        with st.form("studio_add_case"):
            c_id = st.text_input("Case ID", value="SC-2026-99")
            c_title = st.text_input("Case Title", value="Citizens Digital Charter v. Union of India")
            c_year = st.number_input("Decision Year", 1950, 2030, 2026)
            c_domain = st.selectbox("Domain", ["Constitutional Law", "Privacy & Digital Rights", "Fundamental Rights", "Administrative Law"])
            c_status = st.selectbox("Validity Status", ["GOOD_LAW", "DISTINGUISHED", "OVERRULED"])
            c_summary = st.text_area("Ratio Decidendi", value="Applied constitutional proportionality to automated decision systems.")
            c_cited = st.selectbox("Cites Precedent Case", existing_choices)
            c_treat = st.selectbox("Citation Treatment", ["FOLLOWED", "AFFIRMED", "DISTINGUISHED", "OVERRULED"])
            c_note = st.text_input("Judicial Note", value="Followed landmark precedent.")

            if st.form_submit_button("Commit to PostgreSQL", use_container_width=True):
                cited_id = c_cited.split(" ")[0] if c_cited != "None" else None
                add_case_and_citation(
                    {
                        "case_id": c_id.strip(),
                        "case_title": c_title.strip(),
                        "court_level": "Supreme Court of India",
                        "jurisdiction": "Constitutional",
                        "decision_year": int(c_year),
                        "legal_domain": c_domain,
                        "status": c_status,
                        "summary": c_summary.strip(),
                    },
                    cited_id,
                    c_treat,
                    c_note,
                )
                st.success("Committed to PostgreSQL!")
                st.rerun()

    with col_sql:
        st.markdown("#### 🔍 Executed PostgreSQL `WITH RECURSIVE` Query")
        st.code(
            """WITH RECURSIVE citation_tree AS (
    -- Anchor: Direct citations of the selected landmark ruling
    SELECT 1 AS depth,
           c.cited_case_id AS parent_case_id,
           c.citing_case_id AS child_case_id,
           c.treatment,
           ARRAY[c.cited_case_id, c.citing_case_id]::VARCHAR[] AS path_ids
    FROM case_citations c
    WHERE c.cited_case_id = :root_case_id

    UNION ALL

    -- Recursive step: Traverse multi-generational citation chains
    SELECT ct.depth + 1,
           c.cited_case_id,
           c.citing_case_id,
           c.treatment,
           ct.path_ids || c.citing_case_id
    FROM case_citations c
    JOIN citation_tree ct ON c.cited_case_id = ct.child_case_id
    WHERE ct.depth < :max_depth
      AND NOT (c.citing_case_id = ANY(ct.path_ids))
)
SELECT * FROM citation_tree ORDER BY depth ASC;""",
            language="sql",
        )
