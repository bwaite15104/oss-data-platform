"""Feast data source definitions for NBA analytics platform."""

from feast.infra.offline_stores.contrib.postgres_offline_store.postgres_source import (
    PostgreSQLSource,
)

# Source for game features from marts schema
game_features_source = PostgreSQLSource(
    name="game_features",
    query="""
        SELECT 
            game_id,
            game_date,
            home_team_id,
            away_team_id,
            -- Rolling 5-game features
            home_rolling_5_ppg,
            away_rolling_5_ppg,
            home_rolling_5_win_pct,
            away_rolling_5_win_pct,
            home_rolling_5_opp_ppg,
            away_rolling_5_opp_ppg,
            -- Rolling 10-game features
            home_rolling_10_ppg,
            away_rolling_10_ppg,
            home_rolling_10_win_pct,
            away_rolling_10_win_pct,
            -- Feature differences
            ppg_diff_5,
            win_pct_diff_5,
            ppg_diff_10,
            win_pct_diff_10,
            -- Season-level features
            home_season_win_pct,
            away_season_win_pct,
            season_win_pct_diff,
            -- Star player return features
            home_star_players_returning,
            home_key_players_returning,
            home_extended_returns,
            home_total_return_impact,
            home_max_days_since_return,
            away_star_players_returning,
            away_key_players_returning,
            away_extended_returns,
            away_total_return_impact,
            away_max_days_since_return,
            return_impact_diff,
            -- Injury features
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
            injury_advantage_home,
            star_players_out_diff,
            -- Feature interactions
            injury_impact_x_form_diff,
            away_injury_x_form,
            home_injury_x_form,
            home_injury_impact_ratio,
            away_injury_impact_ratio,
            -- Momentum/Streak features
            home_win_streak,
            home_loss_streak,
            away_win_streak,
            away_loss_streak,
            home_momentum_score,
            away_momentum_score,
            -- Rest days features
            home_rest_days,
            home_back_to_back,
            away_rest_days,
            away_back_to_back,
            rest_advantage,
            -- Form divergence features
            home_form_divergence,
            away_form_divergence,
            -- Head-to-head features
            home_h2h_win_pct,
            home_h2h_recent_wins,
            -- Opponent quality features
            home_recent_opp_avg_win_pct,
            away_recent_opp_avg_win_pct,
            home_performance_vs_quality,
            away_performance_vs_quality,
            -- Home/Road performance features
            home_home_win_pct,
            away_road_win_pct,
            home_advantage,
            game_date as event_timestamp
        FROM marts__local.mart_game_features gf
        LEFT JOIN intermediate__local.int_game_momentum_features mf ON mf.game_id = gf.game_id
        WHERE gf.game_date IS NOT NULL
    """,
    timestamp_field="event_timestamp",
    created_timestamp_column="game_date",
)
