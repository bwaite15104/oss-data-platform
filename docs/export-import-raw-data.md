# Export / import raw data for moving machines

Use this when moving to another computer: export only the **raw (source) tables** that downstream transformations depend on, copy the flat files (e.g. to a shared drive), then load them on the new machine.

## Dump cleanup (full-DB dump)

If you started a full `pg_dump` and want to remove it:

- **Inside container**: The container was stopped, so we couldn’t remove `/tmp/nba_analytics.dump`. When you start the stack again, `/tmp` is usually ephemeral, so the file may already be gone. If the container keeps `/tmp` and the file is still there, run:
  `docker exec nba_analytics_postgres rm -f /tmp/nba_analytics.dump`
- **On your machine**: If you ran `docker cp nba_analytics_postgres:/tmp/nba_analytics.dump .`, delete the local file manually (e.g. `nba_analytics.dump` in the repo or current directory).

## Which tables to export (flat files)

These are the **raw_dev** tables that all downstream staging and intermediate models rely on. Exporting these is enough to rebuild transformations on the other PC.

| Table            | Role |
|------------------|------|
| **games**        | NBA game history (backfilled via historical backfill) |
| **injuries**     | Injury history (backfilled via ESPN / ProSportsTransactions scripts) |
| **teams**        | Team reference |
| **players**      | Player reference |
| **boxscores**    | Player boxscores |
| **team_boxscores** | Team boxscores (used by many intermediate models) |

Optional if you need them on the other PC: `betting_odds`, `todays_games` (add to the script’s list if desired).

## Export (this PC)

1. Start the stack so the DB is running (e.g. `make docker-up`).
2. From the repo root (or with `POSTGRES_*` set if DB is in Docker):

   ```bash
   python scripts/export_raw_tables.py --out-dir "D:/shared/nba_export"
   ```

   Default output directory is `data/raw_dev_export/`. Copy that directory (or your `--out-dir`) to the shared drive.

3. If the DB runs in Docker and you run the script on the host, keep `POSTGRES_HOST=localhost`. If you run the script inside a container that talks to the DB over the Docker network, use `--docker` so it uses `POSTGRES_HOST=postgres`.

## Import (other PC)

1. Copy the export directory from the shared drive to the new machine.
2. Start the stack so the DB is running and **raw_dev** schema and tables exist (e.g. run one Dagster ingestion so dlt creates the tables, or create them from your pipeline).
3. Load and replace data:

   ```bash
   python scripts/load_raw_tables.py --data-dir "path/to/raw_dev_export" --truncate
   ```

   Use `POSTGRES_HOST=localhost` if the script runs on the host and the DB is in Docker. Use `--docker` only if the script runs inside a container that uses `postgres` as the host.

4. Run your transformations (e.g. Dagster backfill or SQLMesh) as usual; they will read from the loaded raw tables.
