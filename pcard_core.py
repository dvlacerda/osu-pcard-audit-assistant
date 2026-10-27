"""Core logic for the OSU P-Card audit website.

Kept separate from the Streamlit UI so it can be unit-tested without a browser
or an API key.
"""
from __future__ import annotations

import gzip
import json
import re
import shutil
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

APP_DIR = Path(__file__).resolve().parent
DB_GZ = APP_DIR / "data" / "pcards.db.gz"
DB_PATH = APP_DIR / "data" / "pcards.db"

OSU_AGENCY_NUMBER = 1000
YEARS = [2014, 2013, 2012, 2011, 2010]
MAX_ROWS = 5000            # cap on rows returned to the browser
QUERY_TIMEOUT_S = 15       # abort runaway queries

# Fallback chain for the Gemini API (lecture 6: retry, then move to the next model).
# The first entry can be overridden with the GEMINI_MODEL secret.
DEFAULT_MODELS = ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash"]

SCHEMA_TEXT = """\
Year INTEGER            -- year of TransactionDate (2010-2014)
Month INTEGER           -- month of TransactionDate (1-12)
FullName TEXT           -- cardholder, e.g. 'Employee 5433B965' (anonymised)
ID INTEGER              -- unique transaction id
AgencyNumber INTEGER    -- 1000 = Oklahoma State University
AgencyName TEXT
CardholderLastName TEXT
CardholderFirstInitial TEXT
Description TEXT        -- item description; often just 'GENERAL PURCHASE'
Amount REAL             -- USD; negative = return/credit
Vendor TEXT             -- merchant name as printed on the statement
TransactionDate TEXT    -- text 'M/D/YYYY 0:00:00' (NOT ISO; do not sort it as text)
PostedDate TEXT         -- text 'M/D/YYYY 0:00:00'
MCC TEXT                -- merchant category description, e.g. 'PACKAGE STORES--BEER,WINE,AND LIQUOR'"""

# Suggested keywords for the prohibited-purchase dashboard (all checked to return
# hits in the data, but auditors can type any keyword).
PROHIBITED_CATEGORIES = [
    # (category, description keywords, vendor keywords)
    ("Alcohol", ["WINE", "BEER"], ["LIQUOR", "WINE", "BOTTLE", "BREW"]),
    ("Cash, cash advances, ATM", ["CASH", "ATM"], ["CASH", "ATM"]),
    ("Decorations", ["DECOR", "BALLOON", "HALLOWEEN", "CHRISTMAS"], ["PARTY CITY", "HOBBY LOBBY"]),
    ("Donations and sponsorships", ["DONATION", "SPONSOR"], ["FOUNDATION", "RED CROSS", "CHAMBER"]),
    ("Gasoline", ["UNLEADED", "FUEL"], ["ONCUE", "SHELL", "CASEY", "EXXON"]),
    ("Gifts, gift cards, gift certificates", ["GIFT CARD", "GIFT"], ["GIFT"]),
    ("Insurance", ["INSURANCE"], ["INSURANCE"]),
    ("Late fees", ["LATE FEE", "LATE CHARGE", "PENALTY"], ["LATE"]),
    ("Mail and postage", ["STAMPS", "POSTAGE"], ["USPS", "POST OFFICE", "POSTAL"]),
    ("Moving expenses", ["MOVING"], ["U-HAUL", "MOVING"]),
    ("Personal purchases", ["PERSONAL", "CANDY", "FLOWERS"], ["SALON", "SPA", "FLOWER"]),
    ("Personal memberships and dues", ["MEMBERSHIP", "DUES"], ["ALUMNI", "CHAMBER"]),
    ("Salaries, wages and benefits", ["SALARY", "PAYROLL", "WAGE"], ["PAYROLL"]),
    ("Service and incentive awards", ["AWARD", "PLAQUE", "TROPHY"], ["AWARD", "TROPHY"]),
]

FOLLOW_UP_COLUMNS = ("ID, Amount, FullName, Description, Vendor, TransactionDate, "
                     "PostedDate, MCC")


# ----------------------------------------------------------------------------
# Database
# ----------------------------------------------------------------------------
def ensure_db() -> Path:
    """Decompress the database on first start (the .gz keeps the repo < 100 MB)."""
    if not DB_PATH.exists():
        tmp = DB_PATH.with_suffix(".tmp")
        with gzip.open(DB_GZ, "rb") as src, open(tmp, "wb") as dst:
            shutil.copyfileobj(src, dst)
        tmp.replace(DB_PATH)
    return DB_PATH


_ALLOWED_ACTIONS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION}
_RECURSIVE = getattr(sqlite3, "SQLITE_RECURSIVE", 33)
_ALLOWED_ACTIONS.add(_RECURSIVE)


def _authorizer(action, arg1, arg2, dbname, source):
    """Second line of defence: SQLite itself refuses anything but reading pcards."""
    if action not in _ALLOWED_ACTIONS:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_READ and arg1 not in ("pcards",):
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or ensure_db()
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    con.set_authorizer(_authorizer)
    return con


def run_select(con: sqlite3.Connection, sql: str, params=(), max_rows: int = MAX_ROWS):
    """Run a read-only query with a timeout; returns (DataFrame, truncated?)."""
    deadline = time.monotonic() + QUERY_TIMEOUT_S
    con.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
    try:
        cur = con.execute(sql, params)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchmany(max_rows + 1)
    finally:
        con.set_progress_handler(None, 0)
    truncated = len(rows) > max_rows
    return pd.DataFrame(rows[:max_rows], columns=cols), truncated


# ----------------------------------------------------------------------------
# Tab 2: keyword search (no LLM)
# ----------------------------------------------------------------------------
def like_pattern(keyword: str) -> str:
    esc = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{esc}%"


def keyword_search(con, field_name: str, keyword: str, year: int, limit: int = MAX_ROWS):
    if field_name not in ("Description", "Vendor"):
        raise ValueError("field must be Description or Vendor")
    keyword = keyword.strip()
    if not keyword:
        raise ValueError("Enter a keyword")
    sql = (f"SELECT {FOLLOW_UP_COLUMNS} FROM pcards "
           f"WHERE AgencyNumber = ? AND Year = ? AND {field_name} IS NOT NULL "
           f"AND {field_name} LIKE ? ESCAPE '\\' "
           f"ORDER BY Amount DESC, ID")
    return run_select(con, sql, (OSU_AGENCY_NUMBER, year, like_pattern(keyword)), limit)


# ----------------------------------------------------------------------------
# Tab 1: natural language -> SQL
# ----------------------------------------------------------------------------
_FORBIDDEN = re.compile(
    r"\b(PRAGMA|ATTACH|DETACH|INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|VACUUM|"
    r"REINDEX|TRIGGER|LOAD_EXTENSION|SQLITE_MASTER|SQLITE_SCHEMA)\b", re.I)
_STRING_LITERAL = re.compile(r"'(?:[^']|'')*'")


class UnsafeSQL(ValueError):
    pass


def validate_sql(sql: str) -> str:
    """Accept exactly one read-only SELECT/WITH statement on pcards."""
    s = (sql or "").strip()
    s = re.sub(r"^```(?:sql)?|```$", "", s, flags=re.I).strip()
    s = s.rstrip().rstrip(";").strip()
    if not s:
        raise UnsafeSQL("The model returned no SQL.")
    # Inspect the query with string literals blanked out: MCC values such as
    # 'PACKAGE STORES--BEER,WINE,AND LIQUOR' legitimately contain '--'.
    code = _STRING_LITERAL.sub("''", s)
    if code.count("'") % 2:
        raise UnsafeSQL("Unbalanced quotes in generated SQL.")
    if ";" in code:
        raise UnsafeSQL("Only one statement is allowed.")
    if "--" in code or "/*" in code:
        raise UnsafeSQL("Comments are not allowed in generated SQL.")
    if not re.match(r"^(SELECT|WITH)\b", code, re.I):
        raise UnsafeSQL("Only SELECT queries are allowed.")
    if _FORBIDDEN.search(code):
        raise UnsafeSQL("The query contains a forbidden keyword.")
    if not re.search(r"\bpcards\b", code, re.I):
        raise UnsafeSQL("The query must read the pcards table.")
    return s


def build_prompt(question: str, year: int) -> str:
    return f"""You translate internal auditors' questions into ONE safe SQLite query.

The only approved table is pcards, with these columns:
{SCHEMA_TEXT}

Rules:
- Always restrict to the OSU population: AgencyNumber = {OSU_AGENCY_NUMBER}.
- Unless the question explicitly asks for another year in 2010-2014, use Year = {year}.
- Use Year/Month for periods. For day-level sorting build an ISO date with
  date(printf('%d-%02d-%02d', Year, Month, CAST(substr(TransactionDate, instr(TransactionDate,'/')+1, 2) AS INTEGER))).
- Text matching should be case-insensitive (LIKE is case-insensitive for ASCII).
- When listing transactions include ID, Amount, FullName, Description, Vendor, TransactionDate, MCC.
- Round money to 2 decimals. Add a sensible ORDER BY. Do not add LIMIT unless asked.
- Return exactly one SELECT statement (a WITH clause is allowed). No semicolons, no comments,
  no PRAGMA, no write or schema operations, no ATTACH, no other tables.
- If the question cannot be answered from this table, return sql "" and explain why.

Return JSON with exactly these string keys:
- "sql": the SQLite query
- "explanation": one or two sentences describing the period, filters, grouping and sorting

Auditor question: {question}"""


@dataclass
class LLMResult:
    sql: str
    explanation: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    seconds: float = 0.0
    attempts: list = field(default_factory=list)


def parse_llm_json(text: str) -> tuple[str, str]:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.I).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise ValueError("The model did not return JSON.")
        data = json.loads(m.group(0))
    return str(data.get("sql", "")).strip(), str(data.get("explanation", "")).strip()


def ask_gemini(question: str, year: int, api_key: str, models: list[str] | None = None,
               retries_per_model: int = 2) -> LLMResult:
    """Call Gemini with retry + model fallback; returns SQL and explanation."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    prompt = build_prompt(question, year)
    attempts = []
    start = time.monotonic()
    for model in (models or DEFAULT_MODELS):
        for attempt in range(retries_per_model):
            try:
                resp = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0,
                        max_output_tokens=1024,
                    ),
                )
                sql, explanation = parse_llm_json(resp.text)
                usage = getattr(resp, "usage_metadata", None)
                return LLMResult(
                    sql=sql, explanation=explanation, model=model,
                    input_tokens=getattr(usage, "prompt_token_count", None),
                    output_tokens=getattr(usage, "candidates_token_count", None),
                    seconds=time.monotonic() - start, attempts=attempts)
            except Exception as exc:  # noqa: BLE001 - report and fall back
                attempts.append(f"{model} (try {attempt + 1}): {type(exc).__name__}: {str(exc)[:160]}")
                msg = str(exc).lower()
                if "api key" in msg or "permission" in msg or "unauthenticated" in msg:
                    raise RuntimeError("Gemini rejected the API key.") from exc
                if "not found" in msg or "404" in msg:
                    break  # model name unavailable -> next model immediately
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("No Gemini model returned a valid response:\n" + "\n".join(attempts))
