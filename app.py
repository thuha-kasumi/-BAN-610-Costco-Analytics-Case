import os
from typing import Dict, Any

import pandas as pd
import plotly.express as px
import streamlit as st
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from urllib.parse import quote_plus

st.set_page_config(
    page_title="Costco-Inspired Sales & Inventory Dashboard",
    page_icon="🏬",
    layout="wide",
)

# ------------------------------------------------------------
# Database connection
# ------------------------------------------------------------

def get_db_config() -> Dict[str, Any]:
    """Read database config from Streamlit secrets or environment variables.

    For local pg_hba.conf trust authentication, password can be omitted or blank.
    """
    if "postgres" in st.secrets:
        cfg = dict(st.secrets["postgres"])
    else:
        cfg = {
            "host": os.getenv("PGHOST", "localhost"),
            "port": os.getenv("PGPORT", "5432"),
            "database": os.getenv("PGDATABASE", "costco_analytics"),
            "user": os.getenv("PGUSER", "postgres"),
            "password": os.getenv("PGPASSWORD", ""),
        }
    cfg.setdefault("host", "localhost")
    cfg.setdefault("port", "5432")
    cfg.setdefault("database", "costco_analytics")
    cfg.setdefault("user", "postgres")
    cfg.setdefault("password", "")
    return cfg

@st.cache_resource(show_spinner=False)
def get_engine() -> Engine:
    cfg = get_db_config()
    user = quote_plus(str(cfg["user"]))
    password = str(cfg.get("password", ""))
    host = cfg["host"]
    port = cfg["port"]
    database = cfg["database"]
    sslmode = cfg.get("sslmode", "")
    if password:
        url = f"postgresql+psycopg2://{user}:{quote_plus(password)}@{host}:{port}/{database}"
    else:
        url = f"postgresql+psycopg2://{user}@{host}:{port}/{database}"
    if sslmode:
        url += f"?sslmode={sslmode}"
        return create_engine(url, pool_pre_ping=True)

@st.cache_data(ttl=60, show_spinner=False)
def run_query(sql: str, params: Dict[str, Any] | None = None) -> pd.DataFrame:
    engine = get_engine()
    with engine.connect() as conn:
        return pd.read_sql(text(sql), conn, params=params or {})


def test_connection() -> bool:
    try:
        run_query("SELECT 1 AS ok")
        return True
    except Exception as exc:
        st.error(f"Database connection failed: {exc}")
        return False

# ------------------------------------------------------------
# Helper queries for filters
# ------------------------------------------------------------

def load_filter_values():
    regions = run_query("SELECT DISTINCT region FROM costco_analytics.warehouse ORDER BY region")
    warehouses = run_query("SELECT warehouseid, name FROM costco_analytics.warehouse ORDER BY name")
    categories = run_query("SELECT categoryid, name FROM costco_analytics.category ORDER BY name")
    dates = run_query("SELECT MIN(transactiondate)::date AS min_date, MAX(transactiondate)::date AS max_date FROM costco_analytics.salestransaction")
    return regions, warehouses, categories, dates


def make_params(region, warehouse, category, start_date, end_date):
    return {
        "region": region,
        "warehouse": warehouse,
        "category": category,
        "start_date": start_date,
        "end_date": end_date,
    }

# ------------------------------------------------------------
# Business SQL queries
# ------------------------------------------------------------

CATEGORY_REVENUE_SQL = """
SELECT
    c.name AS category_name,
    c.deptcode AS department_code,
    ROUND(SUM(sti.subtotal)::numeric, 2) AS total_revenue,
    SUM(sti.quantity) AS total_units_sold,
    COUNT(DISTINCT st.transactionid) AS transaction_count
FROM costco_analytics.salestransactionitem sti
JOIN costco_analytics.product p ON sti.productid = p.productid
JOIN costco_analytics.category c ON p.categoryid = c.categoryid
JOIN costco_analytics.salestransaction st ON sti.transactionid = st.transactionid
JOIN costco_analytics.warehouse w ON st.warehouseid = w.warehouseid
WHERE (:region = 'All' OR w.region = :region)
  AND (:warehouse = 'All' OR w.name = :warehouse)
  AND (:category = 'All' OR c.name = :category)
  AND st.transactiondate::date BETWEEN :start_date AND :end_date
GROUP BY c.name, c.deptcode
ORDER BY total_revenue DESC;
"""

WAREHOUSE_PERFORMANCE_SQL = """
SELECT
    w.warehouseid,
    w.name AS warehouse_name,
    w.location,
    w.region,
    ROUND(COALESCE(SUM(st.totalamount), 0)::numeric, 2) AS total_revenue,
    COUNT(st.transactionid) AS transaction_count,
    RANK() OVER (
        PARTITION BY w.region
        ORDER BY COALESCE(SUM(st.totalamount), 0) DESC
    ) AS regional_rank
FROM costco_analytics.warehouse w
LEFT JOIN costco_analytics.salestransaction st ON w.warehouseid = st.warehouseid
WHERE (:region = 'All' OR w.region = :region)
  AND (:warehouse = 'All' OR w.name = :warehouse)
  AND (st.transactiondate IS NULL OR st.transactiondate::date BETWEEN :start_date AND :end_date)
GROUP BY w.warehouseid, w.name, w.location, w.region
ORDER BY total_revenue DESC;
"""

LOW_INVENTORY_SQL = """
SELECT
    w.name AS warehouse_name,
    w.location,
    w.region,
    p.productid,
    p.name AS product_name,
    c.name AS category_name,
    i.stockquantity,
    i.reorderlevel,
    ps.leadtimedays,
    s.name AS supplier_name,
    CASE
        WHEN i.stockquantity = 0 THEN 'Out of Stock'
        WHEN i.stockquantity < i.reorderlevel THEN 'Restock Now'
        WHEN i.stockquantity <= i.reorderlevel + 5 THEN 'Monitor Closely'
        ELSE 'Healthy'
    END AS inventory_status
FROM costco_analytics.inventory i
JOIN costco_analytics.warehouse w ON i.warehouseid = w.warehouseid
JOIN costco_analytics.product p ON i.productid = p.productid
JOIN costco_analytics.category c ON p.categoryid = c.categoryid
LEFT JOIN costco_analytics.productsupplier ps ON p.productid = ps.productid
LEFT JOIN costco_analytics.supplier s ON ps.supplierid = s.supplierid
WHERE (:region = 'All' OR w.region = :region)
  AND (:warehouse = 'All' OR w.name = :warehouse)
  AND (:category = 'All' OR c.name = :category)
  AND i.stockquantity <= i.reorderlevel + 5
ORDER BY
    CASE
        WHEN i.stockquantity = 0 THEN 1
        WHEN i.stockquantity < i.reorderlevel THEN 2
        ELSE 3
    END,
    ps.leadtimedays DESC NULLS LAST;
"""

WAREHOUSE_CATEGORY_SQL = """
SELECT
    w.name AS warehouse_name,
    w.location,
    w.region,
    c.name AS category_name,
    ROUND(SUM(sti.subtotal)::numeric, 2) AS category_revenue,
    SUM(sti.quantity) AS units_sold,
    COUNT(DISTINCT st.transactionid) AS transaction_count
FROM costco_analytics.warehouse w
JOIN costco_analytics.salestransaction st ON w.warehouseid = st.warehouseid
JOIN costco_analytics.salestransactionitem sti ON st.transactionid = sti.transactionid
JOIN costco_analytics.product p ON sti.productid = p.productid
JOIN costco_analytics.category c ON p.categoryid = c.categoryid
WHERE (:region = 'All' OR w.region = :region)
  AND (:warehouse = 'All' OR w.name = :warehouse)
  AND (:category = 'All' OR c.name = :category)
  AND st.transactiondate::date BETWEEN :start_date AND :end_date
GROUP BY w.name, w.location, w.region, c.name
ORDER BY category_revenue ASC;
"""

PROMOTION_SQL = """
SELECT
    w.name AS warehouse_name,
    w.location,
    w.region,
    p.productid,
    p.name AS product_name,
    c.name AS category_name,
    i.stockquantity,
    i.reorderlevel,
    COALESCE(SUM(sti.quantity), 0) AS units_sold,
    p.product_details,
    CASE
        WHEN i.stockquantity > i.reorderlevel
             AND COALESCE(SUM(sti.quantity), 0) = 0
            THEN 'Potential Dead Stock'
        WHEN p.product_details::text ILIKE '%Winter%'
             AND i.stockquantity > i.reorderlevel
            THEN 'Seasonal Promotion Candidate'
        WHEN p.product_details::text ILIKE '%Summer%'
             AND i.stockquantity > i.reorderlevel
            THEN 'Seasonal Inventory Review'
        ELSE 'No Immediate Promotion Needed'
    END AS promotion_recommendation
FROM costco_analytics.inventory i
JOIN costco_analytics.warehouse w ON i.warehouseid = w.warehouseid
JOIN costco_analytics.product p ON i.productid = p.productid
JOIN costco_analytics.category c ON p.categoryid = c.categoryid
LEFT JOIN costco_analytics.salestransactionitem sti ON p.productid = sti.productid
LEFT JOIN costco_analytics.salestransaction st ON sti.transactionid = st.transactionid
WHERE (:region = 'All' OR w.region = :region)
  AND (:warehouse = 'All' OR w.name = :warehouse)
  AND (:category = 'All' OR c.name = :category)
  AND (st.transactiondate IS NULL OR st.transactiondate::date BETWEEN :start_date AND :end_date)
GROUP BY
    w.name, w.location, w.region,
    p.productid, p.name, c.name,
    i.stockquantity, i.reorderlevel, p.product_details
ORDER BY
    CASE
        WHEN
            CASE
                WHEN i.stockquantity > i.reorderlevel AND COALESCE(SUM(sti.quantity), 0) = 0 THEN 'Potential Dead Stock'
                WHEN p.product_details::text ILIKE '%Winter%' AND i.stockquantity > i.reorderlevel THEN 'Seasonal Promotion Candidate'
                WHEN p.product_details::text ILIKE '%Summer%' AND i.stockquantity > i.reorderlevel THEN 'Seasonal Inventory Review'
                ELSE 'No Immediate Promotion Needed'
            END = 'No Immediate Promotion Needed' THEN 2
        ELSE 1
    END,
    i.stockquantity DESC;
"""

# ------------------------------------------------------------
# UI helpers
# ------------------------------------------------------------

def show_metrics(df: pd.DataFrame, metric_col: str, label_prefix: str):
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric(f"{label_prefix} Rows", len(df))
    with c2:
        if not df.empty and metric_col in df:
            st.metric("Total", f"{df[metric_col].sum():,.2f}")
        else:
            st.metric("Total", "0")
    with c3:
        if not df.empty and metric_col in df:
            st.metric("Highest", f"{df[metric_col].max():,.2f}")
        else:
            st.metric("Highest", "0")


def simple_insight(df: pd.DataFrame, name_col: str, value_col: str, subject: str):
    if df.empty or name_col not in df or value_col not in df:
        st.info("No records found for the selected filters.")
        return
    top = df.sort_values(value_col, ascending=False).iloc[0]
    bottom = df.sort_values(value_col, ascending=True).iloc[0]
    st.info(
        f"Business insight: {top[name_col]} is the strongest {subject} in this view "
        f"({top[value_col]:,.2f}), while {bottom[name_col]} is the weakest "
        f"({bottom[value_col]:,.2f})."
    )


def display_table(df: pd.DataFrame):
    st.dataframe(df, use_container_width=True, hide_index=True)

# ------------------------------------------------------------
# Main app
# ------------------------------------------------------------

st.title("🏬 Costco-Inspired Sales & Inventory Insights System")
st.caption("Pre-AI Streamlit dashboard: SQL-driven metrics, tables, and visualizations.")


# Visual framing: make the AI assistant feel like the central copilot,
# while leaving actual AI/API logic for teammate integration.
st.markdown("""
<style>
.ai-callout {
    border: 1px solid #d7e8ff;
    background: linear-gradient(135deg, #f4f9ff 0%, #ffffff 100%);
    padding: 1rem 1.2rem;
    border-radius: 16px;
    box-shadow: 0 4px 14px rgba(0,0,0,0.06);
    margin-bottom: 1rem;
}
.ai-callout h3 {
    margin-top: 0;
    margin-bottom: .35rem;
}
.ai-floating-note {
    position: fixed;
    right: 24px;
    bottom: 24px;
    z-index: 9999;
    max-width: 330px;
    background: #ffffff;
    border: 1px solid #d7e8ff;
    border-radius: 18px;
    padding: 14px 16px;
    box-shadow: 0 8px 28px rgba(0,0,0,0.16);
    font-size: 0.92rem;
}
.ai-floating-note strong {
    color: #0f4c81;
}
</style>
""", unsafe_allow_html=True)

with st.sidebar:
    st.header("Database")
    cfg = get_db_config()
    st.write(f"DB: `{cfg['database']}`")
    st.write(f"User: `{cfg['user']}`")
    st.write(f"Host: `{cfg['host']}:{cfg['port']}`")

if not test_connection():
    st.stop()

regions_df, warehouses_df, categories_df, dates_df = load_filter_values()
regions = ["All"] + regions_df["region"].dropna().tolist()
warehouses = ["All"] + warehouses_df["name"].dropna().tolist()
categories = ["All"] + categories_df["name"].dropna().tolist()

min_date = pd.to_datetime(dates_df.loc[0, "min_date"]).date()
max_date = pd.to_datetime(dates_df.loc[0, "max_date"]).date()

with st.sidebar:
    st.header("Filters")
    region = st.selectbox("Region", regions)
    warehouse = st.selectbox("Warehouse", warehouses)
    category = st.selectbox("Category", categories)
    start_date, end_date = st.date_input(
        "Transaction date range",
        value=(min_date, max_date),
        min_value=min_date,
        max_value=max_date,
    )
    if start_date > end_date:
        st.error("Start date must be before end date.")
        st.stop()

params = make_params(region, warehouse, category, start_date, end_date)

tabs = st.tabs([
    "🤖 AI Copilot",
    "1. Category Winners",
    "2. Warehouse Battle",
    "3. Empty Shelf",
    "4. Hidden Failure",
    "5. Promotion Candidates",
])


with tabs[0]:
    st.subheader("🤖 AI Supply Chain Copilot")
    st.markdown(
        """
        <div class="ai-callout">
        <h3>Central AI Interaction Layer</h3>
        <p>This section is prepared for AI integration. In the final version, users can ask plain-English business questions, and the AI can write SQL, run it against PostgreSQL, then return tables, charts, and recommendations.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    user_question = st.text_input(
        "Ask the AI Copilot a business question",
        placeholder="Example: Which products are low in inventory, and what should we reorder first?",
    )

    quick_questions = [
        "Which product categories generate the most revenue?",
        "Which warehouses are performing best or worst?",
        "Which products are below reorder level?",
        "Which warehouse-category combinations are underperforming?",
        "Which products may need promotional action?",
    ]
    st.caption("Suggested questions for the AI layer")
    st.write(" | ".join([f"`{q}`" for q in quick_questions]))

    if user_question:
        st.info(
            "AI integration pending: teammate can connect an LLM/LangChain function here to generate SQL, execute it, and return a table, chart, and recommendation."
        )

    st.markdown("#### Current Pre-AI Dashboard Path")
    st.write(
        "Users may either start with the AI Copilot above, or manually browse the structured analytics tabs for the 5 agreed business statements."
    )

    st.code(
        """Suggested integration hook:

def get_ai_response(user_question, db_schema, current_filters):
    # 1. Use LLM/LangChain to convert plain English into SQL
    # 2. Run SQL safely against PostgreSQL
    # 3. Return: generated_sql, result_dataframe, chart_config, recommendation_text
    pass
""",
        language="python",
    )

# Floating visual cue for the demo. It is informational only; real chat behavior is in the AI Copilot tab.
st.markdown(
    """
    <div class="ai-floating-note">
        🤖 <strong>AI Copilot Ready</strong><br>
        Ask natural-language questions in the first tab, or review the SQL dashboards manually.
    </div>
    """,
    unsafe_allow_html=True,
)

with tabs[1]:
    st.subheader("1. Big Winners: Category Revenue Leaders")
    df = run_query(CATEGORY_REVENUE_SQL, params)
    show_metrics(df, "total_revenue", "Category")
    display_table(df)
    if not df.empty:
        fig = px.bar(df, x="category_name", y="total_revenue", title="Revenue by Category", text="total_revenue")
        st.plotly_chart(fig, use_container_width=True)
        fig2 = px.pie(df, names="category_name", values="total_revenue", title="Revenue Share by Category")
        st.plotly_chart(fig2, use_container_width=True)
        simple_insight(df, "category_name", "total_revenue", "category")

with tabs[2]:
    st.subheader("2. Location Battle: Warehouse Performance")
    df = run_query(WAREHOUSE_PERFORMANCE_SQL, params)
    show_metrics(df, "total_revenue", "Warehouse")
    display_table(df)
    if not df.empty:
        fig = px.bar(df, x="warehouse_name", y="total_revenue", color="region", title="Revenue by Warehouse", text="total_revenue")
        st.plotly_chart(fig, use_container_width=True)
        simple_insight(df, "warehouse_name", "total_revenue", "warehouse")

with tabs[3]:
    st.subheader("3. Empty Shelf: Low Inventory / Restocking Alerts")
    df = run_query(LOW_INVENTORY_SQL, params)
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("Alerted Items", len(df))
    with c2:
        st.metric("Restock Now", int((df.get("inventory_status", pd.Series(dtype=str)) == "Restock Now").sum()))
    with c3:
        st.metric("Out of Stock", int((df.get("inventory_status", pd.Series(dtype=str)) == "Out of Stock").sum()))
    display_table(df)
    if not df.empty:
        chart_df = df.melt(
            id_vars=["warehouse_name", "product_name"],
            value_vars=["stockquantity", "reorderlevel"],
            var_name="metric",
            value_name="units",
        )
        fig = px.bar(chart_df, x="product_name", y="units", color="metric", barmode="group", title="Stock Quantity vs Reorder Level")
        st.plotly_chart(fig, use_container_width=True)
        st.warning("Items listed here are at or near reorder level and should be reviewed by inventory planners.")

with tabs[4]:
    st.subheader("4. Hidden Failure: Warehouse-Category Underperformance")
    df = run_query(WAREHOUSE_CATEGORY_SQL, params)
    show_metrics(df, "category_revenue", "Warehouse-Category")
    display_table(df)
    if not df.empty:
        df["warehouse_category"] = df["warehouse_name"] + " - " + df["category_name"]
        fig = px.bar(df, x="warehouse_category", y="category_revenue", color="region", title="Warehouse-Category Revenue", text="category_revenue")
        st.plotly_chart(fig, use_container_width=True)
        weakest = df.sort_values("category_revenue", ascending=True).iloc[0]
        st.info(
            f"Business insight: {weakest['warehouse_name']} / {weakest['category_name']} is the lowest-performing combination "
            f"in this view ({weakest['category_revenue']:,.2f})."
        )

with tabs[5]:
    st.subheader("5. Move It or Lose It: Promotional Action Candidates")
    st.caption("Rule-based pre-AI version. Final AI can improve this with inventory age, sales velocity, seasonality, and promotion history.")
    df = run_query(PROMOTION_SQL, params)
    display_table(df)
    if not df.empty:
        fig = px.bar(df, x="product_name", y="stockquantity", color="promotion_recommendation", title="Promotion Candidates by Current Stock")
        st.plotly_chart(fig, use_container_width=True)
        flagged = df[df["promotion_recommendation"] != "No Immediate Promotion Needed"]
        st.metric("Flagged Promotion Candidates", len(flagged))
        if not flagged.empty:
            st.warning("Some products are flagged for promotional review based on current stock, sales, and product details.")
        else:
            st.success("No immediate promotion candidates found under the current rule-based logic.")
