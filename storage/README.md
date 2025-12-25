# Storage

This directory contains data warehouse configuration and initialization scripts.

## PostgreSQL

PostgreSQL initialization script is in `postgres/init.sql`. This script:
- Creates schemas
- Enables extensions
- Creates sample tables
- Inserts sample data

## Database Setup

The database is automatically initialized when starting Docker Compose:

```bash
make docker-up
```

## Sample Data

Sample tables include:
- `public.customers` - Customer data
- `public.orders` - Order data

These are used for development and testing.

