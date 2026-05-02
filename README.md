# Costco-Inspired Sales & Inventory Dashboard

Pre-AI Streamlit dashboard for the BAN 610 database project.

## Setup

1. Run `costco_analytics.sql` in PostgreSQL/pgAdmin.
2. Update `.streamlit/secrets.toml` if your PostgreSQL settings are different.
3. Install dependencies:

```bash
pip install -r requirements.txt
```

4. Run the app:

```bash
streamlit run app.py
```

## Notes

- This version uses manual SQL queries for the five agreed business statements.
- The AI tab is a placeholder for teammate integration.
- If PostgreSQL uses `trust` authentication locally, leave password blank in `secrets.toml`.
- For final demo, the database should ideally contain more rows for richer charts.
