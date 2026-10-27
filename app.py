"""OSU P-Card Audit Assistant - Streamlit app (Part IV of the P-Card case).

Tab 1: ask the database in natural language (Gemini writes SQL, SQLite runs it).
Tab 2: prohibited-purchase dashboard with Description and Vendor keyword search.

The Gemini API key is read from Streamlit secrets / environment, never from code.
"""
import os
import time

import pandas as pd
import streamlit as st

import pcard_core as core

st.set_page_config(page_title="OSU P-Card Audit Assistant", page_icon="🔎", layout="wide")


# ---------------------------------------------------------------- resources
@st.cache_resource(show_spinner="Preparing the P-Card database (first start only)...")
def get_connection():
    return core.connect()


def get_secret(name, default=None):
    try:
        if name in st.secrets:
            return st.secrets[name]
    except Exception:  # no secrets.toml present
        pass
    return os.getenv(name, default)


def money(x):
    return f"${x:,.2f}"


def show_results(df, truncated, file_stem, key):
    if df.empty:
        st.info("No transactions matched.")
        return
    cols = st.columns(3)
    cols[0].metric("Rows", f"{len(df):,}{'+' if truncated else ''}")
    if "Amount" in df.columns:
        cols[1].metric("Total amount", money(pd.to_numeric(df["Amount"], errors="coerce").sum()))
    if "FullName" in df.columns:
        cols[2].metric("Cardholders", f"{df['FullName'].nunique():,}")
    if truncated:
        st.warning(f"Showing the first {core.MAX_ROWS:,} rows. Narrow the search to see everything.")
    st.dataframe(df, width="stretch", hide_index=True,
                 column_config={"Amount": st.column_config.NumberColumn(format="$%.2f")})
    st.download_button("Download results as CSV", df.to_csv(index=False).encode("utf-8"),
                       file_name=f"{file_stem}.csv", mime="text/csv", key=f"dl_{key}")


con = get_connection()

# ---------------------------------------------------------------- header
st.title("OSU P-Card Audit Assistant")
st.caption("Oklahoma State University purchasing-card transactions, 2010-2014 · "
           "course database for the Analytics Mindset P-Card case · read-only access")

tab_ask, tab_dash = st.tabs(["💬 Ask the database", "🚩 Prohibited-purchase dashboard"])

# ================================================================ TAB 1
with tab_ask:
    st.subheader("Ask a question in plain English")
    st.markdown(
        "Type an audit question. Gemini translates it into **one read-only SQLite query**; "
        "the app checks the query, runs it on the database and shows the SQL so you can verify "
        "exactly what was calculated. The language model never sees the transaction data, only "
        "the table structure and your question.")
    c1, c2 = st.columns([1, 3])
    year_q = c1.selectbox("Year (default for the question)", core.YEARS, index=0, key="year_q")
    examples = [
        "Which 10 cardholders spent the most in total?",
        "Total spending per month",
        "Show all transactions at liquor stores",
        "Which vendors received more than $100,000?",
        "List transactions over $5,000 with the cardholder and vendor",
        "How many returns (negative amounts) were there and what was their total?",
    ]
    pick = c2.selectbox("Example questions (optional)", ["—"] + examples, key="example")
    default_q = "" if pick == "—" else pick
    question = st.text_area("Your question", value=default_q, height=90,
                            placeholder="e.g. Which cardholders bought gasoline more than 10 times?")
    go = st.button("Ask", type="primary")

    if go:
        api_key = get_secret("GEMINI_API_KEY")
        if not question.strip():
            st.error("Please type a question.")
        elif not api_key:
            st.error("The site owner has not configured GEMINI_API_KEY in the app secrets.")
        else:
            models = core.DEFAULT_MODELS
            preferred = get_secret("GEMINI_MODEL")
            if preferred:
                models = [preferred] + [m for m in models if m != preferred]
            with st.spinner("Asking Gemini to write the SQL..."):
                try:
                    res = core.ask_gemini(question, year_q, api_key, models)
                except Exception as exc:  # noqa: BLE001
                    st.error(f"The AI service failed: {exc}")
                    res = None
            if res is not None:
                if not res.sql:
                    st.warning(res.explanation or "The question cannot be answered from this table.")
                else:
                    st.markdown(f"**What the query does:** {res.explanation}")
                    st.code(res.sql, language="sql")
                    try:
                        safe_sql = core.validate_sql(res.sql)
                        t0 = time.monotonic()
                        df, truncated = core.run_select(con, safe_sql)
                        db_s = time.monotonic() - t0
                        show_results(df, truncated, "pcard_question_results", "ask")
                    except core.UnsafeSQL as exc:
                        st.error(f"Query blocked by the safety check: {exc}")
                        db_s = None
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"The database could not run the generated query: {exc}")
                        db_s = None
                    tok = (f"{res.input_tokens:,} in / {res.output_tokens:,} out tokens"
                           if res.input_tokens is not None else "tokens n/a")
                    st.caption(f"Model: {res.model} · {tok} · AI {res.seconds:.1f}s"
                               + (f" · database {db_s:.2f}s" if db_s is not None else ""))
                    if "agencynumber" not in res.sql.lower():
                        st.caption("Note: the query does not filter AgencyNumber; the database "
                                   "contains only OSU (1000), so results are unaffected.")

    with st.expander("How this tab keeps the data safe"):
        st.markdown(
            "- The database is opened **read-only** and SQLite's authorizer refuses anything other "
            "than reading the `pcards` table.\n"
            "- Generated SQL must be a single `SELECT`/`WITH` statement with no comments, PRAGMA, "
            "ATTACH or write operations.\n"
            f"- Queries are stopped after {core.QUERY_TIMEOUT_S} seconds and at most "
            f"{core.MAX_ROWS:,} rows are returned.\n"
            "- The Gemini API key is stored in the hosting platform's secrets, not in the code.")

# ================================================================ TAB 2
with tab_dash:
    st.subheader("Prohibited-purchase dashboard")
    with st.expander("📖 Instructions — how to use this dashboard", expanded=True):
        st.markdown(
            "1. **Select the year** you want to audit.\n"
            "2. Choose **one** of the two searches:\n"
            "   - **Description search** looks inside the item description, e.g. `WINE`, "
            "`GIFT CARD`, `MOVING`.\n"
            "   - **Vendor search** looks inside the merchant name, e.g. `USPS`, `LIQUOR`, `U-HAUL`.\n"
            "3. Type a keyword (not case-sensitive, partial words match) or pick a suggested keyword "
            "for a prohibited category, then press **Search**.\n"
            "4. Review the matches: each row has the transaction **ID**, amount, cardholder, "
            "description, vendor, transaction and posting dates and merchant category (MCC), which "
            "is what you need to request receipts and approvals. Download the list as CSV for "
            "follow-up.\n\n"
            "A match is a **potential** exception only: about two-thirds of descriptions just say "
            "'GENERAL PURCHASE', so also try the vendor search, and confirm each item against the "
            "receipt and business purpose before concluding.")

    year_d = st.selectbox("Year to search", core.YEARS, index=0, key="year_d")
    st.markdown("**OSU prohibits P-Card use for:** " + " · ".join(c[0] for c in core.PROHIBITED_CATEGORIES))

    col_desc, col_vend = st.columns(2, gap="large")
    for col, field_name, idx, label, help_txt in [
        (col_desc, "Description", 1, "🔎 Description search",
         "Searches the transaction description (item purchased)."),
        (col_vend, "Vendor", 2, "🏪 Vendor search",
         "Searches the vendor (merchant) name."),
    ]:
        with col:
            with st.container(border=True):
                st.markdown(f"#### {label}")
                st.caption(help_txt)
                category = st.selectbox("Prohibited category (optional, suggests keywords)",
                                        ["—"] + [c[0] for c in core.PROHIBITED_CATEGORIES],
                                        key=f"cat_{field_name}")
                suggestions = []
                if category != "—":
                    row = next(c for c in core.PROHIBITED_CATEGORIES if c[0] == category)
                    suggestions = row[idx]
                sugg = st.selectbox("Suggested keyword", ["—"] + suggestions,
                                    key=f"sugg_{field_name}", disabled=not suggestions)
                kw = st.text_input(f"{field_name} keyword",
                                   value="" if sugg == "—" else sugg,
                                   key=f"kw_{field_name}_{sugg}",
                                   placeholder="e.g. " + ("WINE" if field_name == "Description" else "USPS"))
                if st.button(f"Search {field_name.lower()}", key=f"btn_{field_name}", type="primary"):
                    st.session_state["last_search"] = (field_name, kw, year_d)

    last = st.session_state.get("last_search")
    if last:
        field_name, kw, yr = last
        st.divider()
        st.markdown(f"### Results — {field_name} contains “{kw}” · {yr}")
        try:
            df, truncated = core.keyword_search(con, field_name, kw, yr)
            show_results(df, truncated, f"pcard_{field_name.lower()}_{kw}_{yr}".replace(" ", "_"), "dash")
            if not df.empty:
                with st.expander("Summary by cardholder"):
                    summ = (df.groupby("FullName")
                              .agg(Transactions=("ID", "count"), Total=("Amount", "sum"))
                              .sort_values("Total", ascending=False).reset_index())
                    st.dataframe(summ, width="stretch", hide_index=True,
                                 column_config={"Total": st.column_config.NumberColumn(format="$%.2f")})
        except ValueError as exc:
            st.error(str(exc))

st.divider()
st.caption("Built for the AI in Accounting and Business course (SDU). Data: Oklahoma P-Card "
           "database as modified by the course instructor; names are anonymised. Flags are risk "
           "indicators, not findings of wrongdoing.")
