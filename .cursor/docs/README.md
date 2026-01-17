# Cursor Documentation Guide

This directory contains project context documentation for AI assistants (Cursor, Copilot, etc.).

## Documentation Structure

| File | Purpose | Update When |
|------|---------|-------------|
| `architecture.md` | System design, data flow diagrams | Architecture changes |
| `commands.md` | Make commands, CLI usage, env vars | New commands added |
| `contracts.md` | ODCS schema definitions, type mappings | Schema changes |
| `database.md` | DB schema, tables, queries | Schema/table changes |
| `ingestion.md` | Data sources, dlt pipelines | New data sources |
| `ml-features.md` | ML features, model ideas | ML work |

## Maintenance Rules

### When to Update Docs

1. **Database Changes** → Update `database.md`
   - New schemas created
   - New tables added
   - Column changes
   - Query examples

2. **New Commands** → Update `commands.md`
   - New make targets
   - New CLI commands
   - Environment variable changes

3. **New Data Sources** → Update `ingestion.md`
   - New CDN endpoints
   - New dlt resources
   - New Dagster assets

4. **Schema Changes** → Update `contracts.md`
   - New ODCS contracts
   - Type mapping changes
   - Contract loader changes

5. **Architecture Changes** → Update `architecture.md`
   - New services added
   - Data flow changes
   - Tool stack changes

### Also Update `.cursorrules`

The root `.cursorrules` file should be updated when:
- Quick reference info changes (URLs, credentials)
- Key commands change
- New important code locations added

## Doc Format Guidelines

1. **Keep it scannable** - Use tables, headers, code blocks
2. **Show examples** - Real commands, real queries
3. **Stay current** - Outdated docs are worse than none
4. **Be specific** - Include actual values, not placeholders

## Verification

After updating docs, verify they're accurate:

```bash
# Test commands work
make db-counts
make db-schemas

# Verify queries work
python scripts/db_query.py "SELECT 1"

# Check service URLs respond
curl -s http://localhost:3000 > /dev/null && echo "Dagster OK"
```
