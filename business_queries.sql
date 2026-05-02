-- Business Queries for Streamlit Dashboard
-- Keep separate from the DB creation script so schema/data setup remains clean.
-- These are used by the pre-AI dashboard and can be reused, modified, or replaced by AI-generated SQL later.

SET search_path TO costco_analytics;

-- 1. Big Winners: Category Revenue Leaders
SELECT
    c.name AS category_name,
    c.deptcode AS department_code,
    ROUND(SUM(sti.subtotal)::numeric, 2) AS total_revenue,
    SUM(sti.quantity) AS total_units_sold,
    COUNT(DISTINCT st.transactionid) AS transaction_count
FROM salestransactionitem sti
JOIN product p ON sti.productid = p.productid
JOIN category c ON p.categoryid = c.categoryid
JOIN salestransaction st ON sti.transactionid = st.transactionid
GROUP BY c.name, c.deptcode
ORDER BY total_revenue DESC;

-- 2. Location Battle: Warehouse Performance Ranking
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
FROM warehouse w
LEFT JOIN salestransaction st ON w.warehouseid = st.warehouseid
GROUP BY w.warehouseid, w.name, w.location, w.region
ORDER BY total_revenue DESC;

-- 3. Empty Shelf: Low Inventory / Restocking Alert
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
FROM inventory i
JOIN warehouse w ON i.warehouseid = w.warehouseid
JOIN product p ON i.productid = p.productid
JOIN category c ON p.categoryid = c.categoryid
LEFT JOIN productsupplier ps ON p.productid = ps.productid
LEFT JOIN supplier s ON ps.supplierid = s.supplierid
WHERE i.stockquantity <= i.reorderlevel + 5
ORDER BY
    CASE
        WHEN i.stockquantity = 0 THEN 1
        WHEN i.stockquantity < i.reorderlevel THEN 2
        ELSE 3
    END,
    ps.leadtimedays DESC NULLS LAST;

-- 4. Hidden Failure: Warehouse-Category Underperformance
SELECT
    w.name AS warehouse_name,
    w.location,
    w.region,
    c.name AS category_name,
    ROUND(SUM(sti.subtotal)::numeric, 2) AS category_revenue,
    SUM(sti.quantity) AS units_sold,
    COUNT(DISTINCT st.transactionid) AS transaction_count
FROM warehouse w
JOIN salestransaction st ON w.warehouseid = st.warehouseid
JOIN salestransactionitem sti ON st.transactionid = sti.transactionid
JOIN product p ON sti.productid = p.productid
JOIN category c ON p.categoryid = c.categoryid
GROUP BY w.name, w.location, w.region, c.name
ORDER BY category_revenue ASC;

-- 5. Move It or Lose It: Promotional Action Candidates
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
FROM inventory i
JOIN warehouse w ON i.warehouseid = w.warehouseid
JOIN product p ON i.productid = p.productid
JOIN category c ON p.categoryid = c.categoryid
LEFT JOIN salestransactionitem sti ON p.productid = sti.productid
GROUP BY
    w.name, w.location, w.region,
    p.productid, p.name, c.name,
    i.stockquantity, i.reorderlevel, p.product_details
ORDER BY promotion_recommendation, i.stockquantity DESC;
