import os
import re
import ast
import hashlib
import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor, execute_values
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

REAL_SC_CSV_URL = "https://raw.githubusercontent.com/NoelShallum/Indian_SC_Judgment_database/main/final_judge_database.csv"


def get_database_url() -> str:
    if "DATABASE_URL" in st.secrets:
        return st.secrets["DATABASE_URL"].strip()
    return os.getenv("DATABASE_URL", "").strip()


def get_connection():
    db_url = get_database_url()
    if not db_url:
        raise ValueError("DATABASE_URL is missing. Add it to Streamlit Secrets or environment variables.")
    return psycopg2.connect(db_url, cursor_factory=RealDictCursor)


def make_case_id(title: str) -> str:
    clean = re.sub(r"\s+", " ", str(title).strip().lower())
    digest = hashlib.md5(clean.encode("utf-8")).hexdigest()[:8].upper()
    return f"SC-{digest}"


def extract_year(date_str: str) -> int:
    match = re.search(r"(19\d{2}|20\d{2})", str(date_str))
    return int(match.group(1)) if match else 2000


def parse_cited_list(raw_cited) -> list[str]:
    if pd.isna(raw_cited) or not str(raw_cited).strip():
        return []
    text = str(raw_cited).strip()
    try:
        parsed = ast.literal_eval(text)
        if isinstance(parsed, list):
            return [str(x).strip() for x in parsed if str(x).strip()]
    except Exception:
        pass
    return [x.strip() for x in re.split(r"[;|,]", text) if len(x.strip()) > 4]


def infer_treatment(decision_text: str) -> str:
    txt = str(decision_text).lower()
    if "overrul" in txt:
        return "OVERRULED"
    if "distinguish" in txt:
        return "DISTINGUISHED"
    if "affirm" in txt or "upheld" in txt:
        return "AFFIRMED"
    return "FOLLOWED"


def init_db():
    """Creates relational tables and indexes for court cases and citation edges."""
    ddl = """
    CREATE TABLE IF NOT EXISTS court_cases (
        case_id VARCHAR(32) PRIMARY KEY,
        case_title VARCHAR(500) NOT NULL,
        court_level VARCHAR(100) NOT NULL,
        jurisdiction VARCHAR(100) NOT NULL,
        decision_year INT NOT NULL,
        legal_domain VARCHAR(150) NOT NULL,
        status VARCHAR(32) NOT NULL CHECK (status IN ('GOOD_LAW', 'DISTINGUISHED', 'OVERRULED')),
        summary TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS case_citations (
        citation_id SERIAL PRIMARY KEY,
        citing_case_id VARCHAR(32) NOT NULL REFERENCES court_cases(case_id) ON DELETE CASCADE,
        cited_case_id VARCHAR(32) NOT NULL REFERENCES court_cases(case_id) ON DELETE CASCADE,
        treatment VARCHAR(32) NOT NULL CHECK (treatment IN ('FOLLOWED', 'AFFIRMED', 'DISTINGUISHED', 'OVERRULED')),
        citation_note VARCHAR(300),
        UNIQUE (citing_case_id, cited_case_id)
    );

    CREATE INDEX IF NOT EXISTS idx_citations_cited ON case_citations(cited_case_id);
    CREATE INDEX IF NOT EXISTS idx_citations_citing ON case_citations(citing_case_id);
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(ddl)
        conn.commit()


def load_real_sc_dataset(max_rows: int = 1200):
    """
    Fetches real Indian Supreme Court judgments from GitHub CSV, normalizes
    citing -> cited relationships, and populates PostgreSQL tables.
    """
    init_db()
    df = pd.read_csv(REAL_SC_CSV_URL).dropna(subset=["case title"]).head(max_rows)

    cases_map: dict[str, tuple] = {}
    citation_edges: set[tuple] = set()
    overruled_targets: set[str] = set()

    for _, row in df.iterrows():
        title = str(row.get("case title", "")).strip()[:480]
        if not title:
            continue
        cid = make_case_id(title)
        year = extract_year(row.get("case date", ""))
        bench = str(row.get("bench", "Supreme Court Bench"))[:95]
        issues = str(row.get("issues", "Constitutional & Appellate Review"))[:140]
        decision = str(row.get("decision", "Judgment recorded."))[:1000]

        cases_map[cid] = (
            cid,
            title,
            "Supreme Court of India",
            bench if bench and bench != "nan" else "Supreme Court",
            year,
            issues if issues and issues != "nan" else "General Law",
            "GOOD_LAW",
            decision if decision and decision != "nan" else "Supreme Court ruling.",
        )

        cited_items = parse_cited_list(row.get("cited cases", ""))
        treatment = infer_treatment(decision)

        for cited_title in cited_items[:12]:
            clean_cited = cited_title[:480]
            cited_id = make_case_id(clean_cited)
            if cited_id == cid:
                continue

            if cited_id not in cases_map:
                cited_year = extract_year(clean_cited)
                cases_map[cited_id] = (
                    cited_id,
                    clean_cited,
                    "Supreme Court / High Court Precedent",
                    "Appellate Jurisdiction",
                    cited_year if cited_year <= year else max(1950, year - 5),
                    "Cited Precedent Authority",
                    "GOOD_LAW",
                    "Precedent authority referenced in Supreme Court judgment.",
                )

            citation_edges.add(
                (
                    cid,
                    cited_id,
                    treatment,
                    f"Cited in {title[:120]} ({year})",
                )
            )
            if treatment == "OVERRULED":
                overruled_targets.add(cited_id)

    for oid in overruled_targets:
        if oid in cases_map:
            rec = list(cases_map[oid])
            rec[6] = "OVERRULED"
            cases_map[oid] = tuple(rec)

    with get_connection() as conn:
        with conn.cursor() as cur:
            execute_values(
                cur,
                """
                INSERT INTO court_cases (case_id, case_title, court_level, jurisdiction, decision_year, legal_domain, status, summary)
                VALUES %s
                ON CONFLICT (case_id) DO UPDATE SET
                    case_title = EXCLUDED.case_title,
                    decision_year = LEAST(court_cases.decision_year, EXCLUDED.decision_year),
                    status = EXCLUDED.status;
                """,
                list(cases_map.values()),
            )
            if citation_edges:
                execute_values(
                    cur,
                    """
                    INSERT INTO case_citations (citing_case_id, cited_case_id, treatment, citation_note)
                    VALUES %s
                    ON CONFLICT (citing_case_id, cited_case_id) DO NOTHING;
                    """,
                    list(citation_edges),
                )
        conn.commit()

    return len(cases_map), len(citation_edges)


def fetch_connected_cases(limit: int = 500):
    """Fetches cases ordered by citation connectivity so multi-hop trees appear immediately."""
    query = """
    SELECT c.*,
           COALESCE(in_deg.cited_by_count, 0) AS cited_by_count,
           COALESCE(out_deg.cites_count, 0) AS cites_count
    FROM court_cases c
    LEFT JOIN (
        SELECT cited_case_id, COUNT(*) AS cited_by_count
        FROM case_citations GROUP BY cited_case_id
    ) in_deg ON c.case_id = in_deg.cited_case_id
    LEFT JOIN (
        SELECT citing_case_id, COUNT(*) AS cites_count
        FROM case_citations GROUP BY citing_case_id
    ) out_deg ON c.case_id = out_deg.citing_case_id
    ORDER BY (COALESCE(in_deg.cited_by_count, 0) + COALESCE(out_deg.cites_count, 0)) DESC, c.decision_year DESC
    LIMIT %s;
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (limit,))
            return cur.fetchall()


def add_case_and_citation(case_data: dict, cited_case_id: str | None, treatment: str, note: str):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO court_cases (case_id, case_title, court_level, jurisdiction, decision_year, legal_domain, status, summary)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (case_id) DO UPDATE SET
                    case_title = EXCLUDED.case_title,
                    status = EXCLUDED.status,
                    summary = EXCLUDED.summary;
                """,
                (
                    case_data["case_id"],
                    case_data["case_title"],
                    case_data["court_level"],
                    case_data["jurisdiction"],
                    case_data["decision_year"],
                    case_data["legal_domain"],
                    case_data["status"],
                    case_data["summary"],
                ),
            )
            if cited_case_id and cited_case_id != "None":
                cur.execute(
                    """
                    INSERT INTO case_citations (citing_case_id, cited_case_id, treatment, citation_note)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (citing_case_id, cited_case_id) DO UPDATE SET
                        treatment = EXCLUDED.treatment,
                        citation_note = EXCLUDED.citation_note;
                    """,
                    (case_data["case_id"], cited_case_id, treatment, note),
                )
                if treatment == "OVERRULED":
                    cur.execute(
                        "UPDATE court_cases SET status = 'OVERRULED' WHERE case_id = %s;",
                        (cited_case_id,),
                    )
        conn.commit()


def trace_downstream_precedents(root_case_id: str, max_depth: int = 6):
    """PostgreSQL WITH RECURSIVE CTE tracing downstream citing cases across generations."""
    query = """
    WITH RECURSIVE citation_tree AS (
        SELECT
            1 AS depth,
            c.cited_case_id AS parent_case_id,
            p.case_title AS parent_title,
            c.citing_case_id AS child_case_id,
            ch.case_title AS child_title,
            ch.decision_year AS child_year,
            ch.status AS child_status,
            c.treatment,
            c.citation_note,
            ARRAY[c.cited_case_id, c.citing_case_id]::VARCHAR[] AS path_ids
        FROM case_citations c
        JOIN court_cases p ON p.case_id = c.cited_case_id
        JOIN court_cases ch ON ch.case_id = c.citing_case_id
        WHERE c.cited_case_id = %s

        UNION ALL

        SELECT
            ct.depth + 1 AS depth,
            c.cited_case_id AS parent_case_id,
            p.case_title AS parent_title,
            c.citing_case_id AS child_case_id,
            ch.case_title AS child_title,
            ch.decision_year AS child_year,
            ch.status AS child_status,
            c.treatment,
            c.citation_note,
            ct.path_ids || c.citing_case_id
        FROM case_citations c
        JOIN citation_tree ct ON c.cited_case_id = ct.child_case_id
        JOIN court_cases p ON p.case_id = c.cited_case_id
        JOIN court_cases ch ON ch.case_id = c.citing_case_id
        WHERE ct.depth < %s
          AND NOT (c.citing_case_id = ANY(ct.path_ids))
    )
    SELECT * FROM citation_tree ORDER BY depth ASC, child_year ASC;
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (root_case_id, max_depth))
            return cur.fetchall()


def trace_upstream_authorities(target_case_id: str, max_depth: int = 6):
    """PostgreSQL WITH RECURSIVE CTE walking backward to uncover foundational authorities."""
    query = """
    WITH RECURSIVE authority_ancestors AS (
        SELECT
            1 AS depth,
            c.citing_case_id AS child_case_id,
            ch.case_title AS child_title,
            c.cited_case_id AS ancestor_case_id,
            anc.case_title AS ancestor_title,
            anc.decision_year AS ancestor_year,
            anc.status AS ancestor_status,
            c.treatment,
            c.citation_note,
            ARRAY[c.citing_case_id, c.cited_case_id]::VARCHAR[] AS path_ids
        FROM case_citations c
        JOIN court_cases ch ON ch.case_id = c.citing_case_id
        JOIN court_cases anc ON anc.case_id = c.cited_case_id
        WHERE c.citing_case_id = %s

        UNION ALL

        SELECT
            aa.depth + 1 AS depth,
            c.citing_case_id AS child_case_id,
            ch.case_title AS child_title,
            c.cited_case_id AS ancestor_case_id,
            anc.case_title AS ancestor_title,
            anc.decision_year AS ancestor_year,
            anc.status AS ancestor_status,
            c.treatment,
            c.citation_note,
            aa.path_ids || c.cited_case_id
        FROM case_citations c
        JOIN authority_ancestors aa ON c.citing_case_id = aa.ancestor_case_id
        JOIN court_cases ch ON ch.case_id = c.citing_case_id
        JOIN court_cases anc ON anc.case_id = c.cited_case_id
        WHERE aa.depth < %s
          AND NOT (c.cited_case_id = ANY(aa.path_ids))
    )
    SELECT * FROM authority_ancestors ORDER BY depth ASC, ancestor_year DESC;
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (target_case_id, max_depth))
            return cur.fetchall()
