-- Staging model for customers
MODEL (
  name staging.stg_customers,
  kind VIEW
);

SELECT
  customer_id,
  email,
  first_name,
  last_name,
  created_at,
  updated_at,
  status
FROM public.customers;

