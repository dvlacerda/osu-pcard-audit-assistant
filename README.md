# OSU P-Card Audit Assistant

Part IV of the *Analytics Mindset – P-Card* case (AI in Accounting and Business, SDU).

A Streamlit website with two tabs for internal auditors:

1. **Ask the database** – type a question in plain English; Gemini writes one read-only SQLite
   query, the app validates and runs it, and shows the explanation, the SQL, the results, token use
   and timing, plus a CSV download.
2. **Prohibited-purchase dashboard** – instructions, a year selector and two clearly labelled
   searches (**Description search** and **Vendor search**) with suggested keywords for each
   prohibited category. Results list the ID, amount, cardholder, description, vendor, dates and
   MCC needed for follow-up, with summary metrics and CSV download. No LLM is used in this tab.

## Security

- The Gemini API key is **never in the code or the repository**. It is read from Streamlit
  secrets (`GEMINI_API_KEY`) or an environment variable. `.streamlit/secrets.toml` and `.env` are
  git-ignored.
- The database is opened read-only; SQLite's authorizer denies everything except reading the
  `pcards` table; generated SQL must be a single `SELECT`/`WITH` without comments, PRAGMA, ATTACH or
  writes; queries time out after 15 s and return at most 5,000 rows.
- The dashboard uses parameterised `LIKE` queries (user input is never pasted into SQL).

## Run locally

```bash
python -m venv .venv
# Windows: .\.venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # then paste your key
streamlit run app.py
```

The database ships compressed as `data/pcards.db.gz` (22 MB, because GitHub refuses files over
100 MB) and is unpacked automatically on first start.

## Deploy (Streamlit Community Cloud)

1. Push this folder to a public GitHub repository.
2. Go to <https://share.streamlit.io>, sign in with GitHub, choose **Create app → Deploy a public app
   from GitHub**, select the repository, branch `main` and main file `app.py`.
3. In **Advanced settings → Secrets**, paste `GEMINI_API_KEY = "..."` and deploy.

## Tests

```bash
pip install pytest && python -m pytest -q
```

The tests run every assignment query from Parts II-III through the safety checks, try a set of
malicious SQL strings, test the keyword search, and exercise the Gemini retry/fallback logic with a
fake client.
