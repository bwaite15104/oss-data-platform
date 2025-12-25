-- Mart model: customer orders aggregation
MODEL (
  name marts.customer_orders,
  kind VIEW
);

SELECT
  c.customer_id,
  c.email,
  c.first_name,
  c.last_name,
  COUNT(o.order_id) as total_orders,
  SUM(o.total_amount) as total_spent,
  MAX(o.order_date) as last_order_date
FROM staging.stg_customers c
LEFT JOIN public.orders o ON c.customer_id = o.customer_id
GROUP BY c.customer_id, c.email, c.first_name, c.last_name;

