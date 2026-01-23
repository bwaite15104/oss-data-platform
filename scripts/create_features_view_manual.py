"""Manually create features_dev.game_features view from marts.mart_game_features."""

import psycopg2
from psycopg2.extras import RealDictCursor

conn = psycopg2.connect(
    host='localhost',
    port=5432,
    database='nba_analytics',
    user='postgres',
    password='postgres',
)
cursor = conn.cursor(cursor_factory=RealDictCursor)

# Check if marts.mart_game_features has the interaction features
cursor.execute("""
    SELECT column_name 
    FROM information_schema.columns 
    WHERE table_schema = 'marts' 
      AND table_name = 'mart_game_features'
      AND column_name IN ('injury_impact_x_form_diff', 'away_injury_x_form', 'home_injury_x_form')
""")
cols = [r['column_name'] for r in cursor.fetchall()]
print(f"marts.mart_game_features has {len(cols)} interaction features")

if len(cols) >= 3:
    # Get all columns from marts.mart_game_features that should be in features_dev.game_features
    # Based on the SELECT statement in game_features.sql
    cursor.execute("""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_schema = 'marts' 
          AND table_name = 'mart_game_features'
        ORDER BY ordinal_position
    """)
    all_cols = [r['column_name'] for r in cursor.fetchall()]
    
    # Select only the columns that are in game_features.sql
    # This is a simplified version - in reality we'd parse the SQL
    # But for now, let's just create a view that selects all columns
    print("\nCreating features_dev.game_features view...")
    cursor.execute("DROP VIEW IF EXISTS features_dev.game_features CASCADE")
    
    # Create view with explicit column selection matching game_features.sql
    cursor.execute("""
        CREATE VIEW features_dev.game_features AS
        SELECT 
            game_id,
            game_date,
            home_team_id,
            away_team_id,
            home_win,
            home_score,
            away_score,
            point_spread,
            total_points,
            is_overtime,
            home_rolling_5_ppg,
            home_rolling_5_opp_ppg,
            home_rolling_5_win_pct,
            home_rolling_5_apg,
            home_rolling_5_rpg,
            home_rolling_5_fg_pct,
            home_rolling_5_fg3_pct,
            home_rolling_10_ppg,
            home_rolling_10_win_pct,
            away_rolling_5_ppg,
            away_rolling_5_opp_ppg,
            away_rolling_5_win_pct,
            away_rolling_5_apg,
            away_rolling_5_rpg,
            away_rolling_5_fg_pct,
            away_rolling_5_fg3_pct,
            away_rolling_10_ppg,
            away_rolling_10_win_pct,
            ppg_diff_5,
            win_pct_diff_5,
            fg_pct_diff_5,
            ppg_diff_10,
            win_pct_diff_10,
            home_season_win_pct,
            home_season_point_diff,
            home_wins_at_home,
            home_losses_at_home,
            away_season_win_pct,
            away_season_point_diff,
            away_wins_on_road,
            away_losses_on_road,
            season_win_pct_diff,
            season_point_diff_diff,
            home_star_players_returning,
            home_key_players_returning,
            home_extended_returns,
            home_total_return_impact,
            home_max_days_since_return,
            home_avg_days_since_return,
            home_has_star_return,
            home_has_extended_return,
            away_star_players_returning,
            away_key_players_returning,
            away_extended_returns,
            away_total_return_impact,
            away_max_days_since_return,
            away_avg_days_since_return,
            away_has_star_return,
            away_has_extended_return,
            star_return_advantage,
            return_impact_diff,
            home_star_players_out,
            home_key_players_out,
            home_star_players_doubtful,
            home_key_players_doubtful,
            home_star_players_questionable,
            home_injury_impact_score,
            home_injured_players_count,
            home_has_key_injury,
            away_star_players_out,
            away_key_players_out,
            away_star_players_doubtful,
            away_key_players_doubtful,
            away_star_players_questionable,
            away_injury_impact_score,
            away_injured_players_count,
            away_has_key_injury,
            injury_impact_diff,
            star_players_out_diff,
            injury_impact_x_form_diff,
            away_injury_x_form,
            home_injury_x_form,
            home_injury_impact_ratio,
            away_injury_impact_ratio,
            home_injury_penalty_severe,
            away_injury_penalty_severe,
            home_injury_penalty_absolute,
            away_injury_penalty_absolute,
            CURRENT_TIMESTAMP as updated_at
        FROM marts.mart_game_features
    """)
    conn.commit()
    print("View created successfully!")
    
    # Verify
    cursor.execute("""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_schema = 'features_dev' 
          AND table_name = 'game_features'
          AND column_name IN ('injury_impact_x_form_diff', 'away_injury_x_form', 'home_injury_x_form')
    """)
    verify_cols = [r['column_name'] for r in cursor.fetchall()]
    print(f"Verification: View has {len(verify_cols)} interaction features")
else:
    print("ERROR: marts.mart_game_features does not have interaction features")

cursor.close()
conn.close()
