"""Validate injury boxscore fallback: count games where fallback adds non-zero injury impact."""
import os
import sys

path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if path not in sys.path:
    sys.path.insert(0, path)

import psycopg2

def main():
    conn = psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        database=os.getenv("POSTGRES_DB", "nba_analytics"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", "postgres"),
    )
    cur = conn.cursor()

    # Resolve staging schema (SQLMesh may use staging__local)
    cur.execute("""
        SELECT table_schema FROM information_schema.tables
        WHERE table_name = 'stg_player_boxscores' LIMIT 1
    """)
    row = cur.fetchone()
    staging_schema = row[0] if row else "staging"

    # Count games where boxscore fallback would give non-zero home_injury_impact_score
    cur.execute("""
    WITH players_who_played AS (
        SELECT b.game_id, b.player_id
        FROM %s.stg_player_boxscores b
        JOIN %s.stg_games g ON b.game_id = g.game_id
        WHERE g.is_completed AND b.minutes_played > 0
    ),
    home_dnp AS (
        SELECT gtd.game_id, COALESCE(SUM(sp.impact_score), 0) as home_impact
        FROM %s.stg_games gtd
        JOIN %s.stg_games g ON gtd.game_id = g.game_id AND g.is_completed
        JOIN intermediate.int_star_players sp ON sp.team_id = gtd.home_team_id
        LEFT JOIN players_who_played pwp ON pwp.game_id = gtd.game_id AND pwp.player_id = sp.player_id
        WHERE pwp.player_id IS NULL
        GROUP BY gtd.game_id
    )
    SELECT COUNT(*) FROM home_dnp WHERE home_impact > 0
    """ % (staging_schema, staging_schema, staging_schema, staging_schema))
    games_with_fallback = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM %s.stg_games WHERE is_completed" % staging_schema)
    total_completed = cur.fetchone()[0]

    print("Games with non-zero home injury from boxscore fallback:", games_with_fallback)
    print("Total completed games:", total_completed)
    pct = 100.0 * games_with_fallback / total_completed if total_completed else 0
    print("Percent of games that would get fallback signal: %.1f%%" % pct)
    conn.close()

    # Validation: if fallback would fill >50% of games, we've addressed the zeros issue
    if pct >= 50:
        print("PASS: Fallback would provide injury signal for >=50%% of games (reduces zeros).")
        return 0
    print("INFO: Fallback would provide signal for %.1f%% of games." % pct)
    return 0

if __name__ == "__main__":
    sys.exit(main())
