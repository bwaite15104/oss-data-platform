#!/usr/bin/env python3
"""
Validate that key datasets and feature/training assets have the data needed for training.

Run after backfill to verify:
- features_dev.game_features has expected date range and row counts
- No large gaps by season
- intermediate.int_game_momentum_features covers all training games (JOIN completeness)
- Column count and training query return rows

Usage:
  python scripts/validate_training_data.py
  docker exec nba_analytics_dagster_webserver python /app/scripts/validate_training_data.py
"""

import os
import sys
from pathlib import Path

# Allow running from project root or from scripts/
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

try:
    from scripts.db_query import get_connection, run_query
except ImportError:
    try:
        from db_query import get_connection, run_query
    except ImportError:
        get_connection = run_query = None

TRAINING_START = "2010-10-01"


def _run(query: str, timeout_seconds: int = 120):
    """Run SELECT and return list of dicts; on error raise."""
    if run_query is None:
        import psycopg2
        from psycopg2.extras import RealDictCursor
        conn = psycopg2.connect(
            host=os.getenv("POSTGRES_HOST", "localhost"),
            port=int(os.getenv("POSTGRES_PORT", "5432")),
            database=os.getenv("POSTGRES_DB", "nba_analytics"),
            user=os.getenv("POSTGRES_USER", "postgres"),
            password=os.getenv("POSTGRES_PASSWORD", "postgres"),
            connect_timeout=10,
        )
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(query)
                return [dict(row) for row in cur.fetchall()]
        finally:
            conn.close()
    cols, rows = run_query(query, as_dict=True, timeout_seconds=timeout_seconds or 120)
    if rows and hasattr(rows[0], "keys"):
        return [dict(r) for r in rows]
    if cols and rows:
        return [dict(zip(cols, r)) for r in rows]
    return rows or []


def main():
    print("=" * 70)
    print("TRAINING DATA VALIDATION")
    print("(Training uses game_date >= 2010-10-01 from features_dev.game_features)")
    print("=" * 70)

    errors = []
    warnings = []

    # 1. features_dev.game_features: date range and row counts
    print("\n1. features_dev.game_features — date range and trainable rows")
    try:
        rows = _run("""
            SELECT
              MIN(game_date::date) AS min_date,
              MAX(game_date::date) AS max_date,
              COUNT(*) AS total_rows,
              COUNT(*) FILTER (
                WHERE game_date::date >= '""" + TRAINING_START + """'
                  AND home_score IS NOT NULL AND away_score IS NOT NULL AND home_win IS NOT NULL
              ) AS trainable_rows
            FROM features_dev.game_features
        """)
    except Exception as e:
        if "does not exist" in str(e).lower() or "relation" in str(e).lower():
            errors.append("features_dev.game_features does not exist (view/table missing).")
        else:
            errors.append(f"features_dev.game_features query failed: {e}")
        rows = []
    if rows:
        r = rows[0]
        min_d, max_d, total, trainable = r.get("min_date"), r.get("max_date"), r.get("total_rows"), r.get("trainable_rows")
        print(f"   min_date: {min_d}, max_date: {max_d}, total_rows: {total}, trainable_rows: {trainable}")
        if total == 0:
            errors.append("features_dev.game_features is empty.")
        else:
            if min_d and str(min_d) > TRAINING_START:
                warnings.append(f"game_features min_date {min_d} is after training start {TRAINING_START}.")
            if trainable == 0 and total and int(total) > 0:
                warnings.append("No trainable rows (check home_score, away_score, home_win non-null and date >= 2010-10-01).")
            elif trainable is not None and int(trainable) < 10000:
                warnings.append(f"Trainable rows ({trainable}) is low; typical full history is tens of thousands.")

    # 2. Rows per season
    print("\n2. features_dev.game_features — rows per season (trainable only)")
    try:
        rows2 = _run("""
            SELECT
              EXTRACT(YEAR FROM game_date)::int AS season_year,
              COUNT(*) AS rows
            FROM features_dev.game_features
            WHERE game_date::date >= '""" + TRAINING_START + """'
              AND home_score IS NOT NULL AND away_score IS NOT NULL AND home_win IS NOT NULL
            GROUP BY 1
            ORDER BY 1
        """)
    except Exception as e:
        errors.append(f"Per-season query failed: {e}")
        rows2 = []
    if rows2:
        seasons = [(r.get("season_year"), r.get("rows")) for r in rows2]
        print(f"   Seasons: {len(seasons)} (first 3 and last 3)")
        for (y, c) in seasons[:3]:
            print(f"      {y}: {c} rows")
        if len(seasons) > 6:
            print("      ...")
        for (y, c) in seasons[-3:]:
            print(f"      {y}: {c} rows")
        zero_seasons = [y for y, c in seasons if c == 0]
        if zero_seasons:
            warnings.append(f"Seasons with 0 rows: {zero_seasons}")

    # 3. Momentum JOIN completeness
    print("\n3. Momentum JOIN — games in training range missing from int_game_momentum_features")
    try:
        rows3 = _run("""
            SELECT COUNT(*) AS missing_momentum
            FROM features_dev.game_features gf
            LEFT JOIN intermediate.int_game_momentum_features mf ON mf.game_id = gf.game_id
            WHERE gf.game_date::date >= '""" + TRAINING_START + """'
              AND gf.game_date::date < CURRENT_DATE
              AND gf.home_score IS NOT NULL AND gf.away_score IS NOT NULL AND gf.home_win IS NOT NULL
              AND mf.game_id IS NULL
        """)
    except Exception as e:
        if "does not exist" in str(e).lower():
            errors.append("intermediate.int_game_momentum_features or features_dev.game_features missing.")
        else:
            errors.append(f"Momentum JOIN check failed: {e}")
        rows3 = []
    if rows3:
        missing = rows3[0].get("missing_momentum")
        print(f"   missing_momentum: {missing}")
        if missing is not None and int(missing) > 0:
            warnings.append(f"{missing} training-range games lack momentum data (LEFT JOIN will produce NULLs).")

    # 4. Column count
    print("\n4. features_dev.game_features — column count")
    try:
        rows4 = _run("""
            SELECT COUNT(*) AS col_count
            FROM information_schema.columns
            WHERE table_schema = 'features_dev' AND table_name = 'game_features'
        """, timeout_seconds=30)
    except Exception as e:
        errors.append(f"Column count query failed: {e}")
        rows4 = []
    if rows4:
        cnt = rows4[0].get("col_count")
        print(f"   columns: {cnt}")
        if cnt is not None and int(cnt) < 50:
            warnings.append(f"Low column count ({cnt}); training expects many feature columns.")

    # 5. Training query dry run
    print("\n5. Training query dry run — row count with same filters as training")
    try:
        rows5 = _run("""
            SELECT COUNT(*) AS cnt
            FROM features_dev.game_features gf
            LEFT JOIN intermediate.int_game_momentum_features mf ON mf.game_id = gf.game_id
            WHERE gf.game_date IS NOT NULL
              AND gf.home_score IS NOT NULL AND gf.away_score IS NOT NULL AND gf.home_win IS NOT NULL
              AND gf.game_date::date >= '2010-10-01'::date
              AND gf.game_date::date < CURRENT_DATE
        """)
    except Exception as e:
        errors.append(f"Training dry-run query failed: {e}")
        rows5 = []
    if rows5:
        cnt5 = rows5[0].get("cnt")
        print(f"   rows: {cnt5}")
        if cnt5 is not None and int(cnt5) == 0:
            errors.append("Training query returns 0 rows; training will fail.")

    # 6. Optional: injury column zeros
    print("\n6. Optional — injury column (home_injury_penalty_absolute) zeros")
    try:
        rows6 = _run("""
            SELECT
              COUNT(*) AS total,
              SUM(CASE WHEN home_injury_penalty_absolute = 0 OR home_injury_penalty_absolute IS NULL THEN 1 ELSE 0 END) AS zeros_or_null
            FROM features_dev.game_features
            WHERE game_date::date >= '2010-10-01'
        """, timeout_seconds=60)
    except Exception:
        print("   (column may not exist or table not ready — skip)")
        rows6 = []
    if rows6:
        total6 = rows6[0].get("total")
        zeros6 = rows6[0].get("zeros_or_null")
        if total6 and int(total6) > 0 and zeros6 is not None:
            pct = 100.0 * int(zeros6) / int(total6)
            print(f"   total: {total6}, zeros_or_null: {zeros6} ({pct:.1f}%)")
            if pct > 90:
                warnings.append("Most rows have zero/null injury penalty; injury features may not be populated.")

    # Summary
    print("\n" + "=" * 70)
    if errors:
        print("FAILED — errors:")
        for e in errors:
            print(f"  - {e}")
    if warnings:
        print("Warnings:")
        for w in warnings:
            print(f"  - {w}")
    if not errors:
        print("OK — no blocking errors. Training data checks passed.")
        if warnings:
            print("  (Address warnings if you expect full history or specific columns.)")
    print("=" * 70)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
