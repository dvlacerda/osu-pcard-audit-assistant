"""Tests for pcard_core (run: python -m pytest -q)."""
import re
import sqlite3
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pcard_core as core  # noqa: E402


@pytest.fixture(scope="module")
def con():
    return core.connect()


def test_db_is_osu_only(con):
    df, _ = core.run_select(con, "SELECT DISTINCT AgencyNumber FROM pcards")
    assert df.AgencyNumber.tolist() == [1000]


@pytest.mark.parametrize("bad", [
    "DROP TABLE pcards",
    "SELECT * FROM pcards; DELETE FROM pcards",
    "SELECT * FROM sqlite_master",
    "ATTACH DATABASE 'x.db' AS x",
    "PRAGMA table_info(pcards)",
    "SELECT * FROM pcards -- WHERE Year = 2014",
    "SELECT * FROM pcards /* hidden */",
    "UPDATE pcards SET Amount = 0",
    "SELECT 1",
    "SELECT * FROM pcards WHERE Vendor = 'x",
])
def test_validate_rejects_unsafe(bad):
    with pytest.raises(core.UnsafeSQL):
        core.validate_sql(bad)


def test_validate_allows_double_dash_inside_literals():
    sql = "SELECT * FROM pcards WHERE MCC = 'PACKAGE STORES--BEER,WINE,AND LIQUOR';"
    assert core.validate_sql(sql).endswith("LIQUOR'")


def test_authorizer_blocks_even_if_validation_bypassed(con):
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("SELECT name FROM sqlite_master").fetchall()
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("DELETE FROM pcards")


def _assignment_queries():
    sql_file = ROOT / "pcard_queries.sql"
    if not sql_file.exists():
        return {}
    text = sql_file.read_text(encoding="utf-8")
    parts = re.split(r"^-- name: (\S+)\s*$", text, flags=re.M)
    return {parts[i]: _strip_comments(parts[i + 1]) for i in range(1, len(parts), 2)}


def _strip_comments(sql):
    """Remove -- comments that are outside string literals."""
    out, in_str, i = [], False, 0
    while i < len(sql):
        ch = sql[i]
        if ch == "'":
            in_str = not in_str
        if not in_str and sql.startswith("--", i):
            while i < len(sql) and sql[i] != "\n":
                i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


EXPECTED_ROWS = {
    "Question1": 127, "Question3": 33, "Question4": 254, "Question8": 12,
    "qry_T3_Question1": 9, "qry_T3_Question2": 4924, "qry_T3_Question4": 160,
}


@pytest.mark.parametrize("name,sql", list(_assignment_queries().items()))
def test_assignment_queries_pass_guardrails(con, name, sql):
    safe = core.validate_sql(sql)
    df, _ = core.run_select(con, safe, max_rows=100000)
    if name in EXPECTED_ROWS:
        assert len(df) == EXPECTED_ROWS[name]


def test_keyword_search(con):
    df, trunc = core.keyword_search(con, "Vendor", "usps", 2014)
    assert len(df) > 0 and not trunc
    assert df.Vendor.str.upper().str.contains("USPS").all()
    assert list(df.columns) == [c.strip() for c in core.FOLLOW_UP_COLUMNS.split(",")]


def test_keyword_search_escapes_wildcards(con):
    df, _ = core.keyword_search(con, "Description", "%", 2014)
    assert df.Description.str.contains("%", regex=False).all()


def test_keyword_search_rejects_bad_field(con):
    with pytest.raises(ValueError):
        core.keyword_search(con, "Amount; DROP", "x", 2014)


def test_parse_llm_json_handles_fences():
    sql, exp = core.parse_llm_json('```json\n{"sql": "SELECT 1 FROM pcards", "explanation": "x"}\n```')
    assert sql == "SELECT 1 FROM pcards" and exp == "x"


def test_ask_gemini_with_fake_client(monkeypatch):
    """Exercise the retry/fallback path without network access."""
    calls = []

    class FakeModels:
        def generate_content(self, model, contents, config):
            calls.append(model)
            if model == "bad-model":
                raise RuntimeError("404 model not found")
            usage = types.SimpleNamespace(prompt_token_count=900, candidates_token_count=60)
            return types.SimpleNamespace(
                text='{"sql": "SELECT FullName FROM pcards WHERE AgencyNumber = 1000 AND Year = 2014",'
                     ' "explanation": "ok"}', usage_metadata=usage)

    class FakeClient:
        def __init__(self, api_key):
            self.models = FakeModels()

    fake_genai = types.ModuleType("google.genai")
    fake_genai.Client = FakeClient
    fake_types = types.ModuleType("google.genai.types")
    fake_types.GenerateContentConfig = lambda **kw: kw
    fake_genai.types = fake_types
    google_pkg = types.ModuleType("google")
    google_pkg.genai = fake_genai
    monkeypatch.setitem(sys.modules, "google", google_pkg)
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)
    monkeypatch.setitem(sys.modules, "google.genai.types", fake_types)

    res = core.ask_gemini("Who spent most?", 2014, "key", models=["bad-model", "good-model"])
    assert res.model == "good-model" and res.input_tokens == 900
    assert calls == ["bad-model", "good-model"]
