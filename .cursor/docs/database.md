# Database Schema

## Connection Details
```
Host: localhost (or 'postgres' from Docker network)
Port: 5432
Database: oss_data_platform
Username: postgres
Password: postgres
Schema: nba
```

## Connection String
```
postgresql://postgres:postgres@localhost:5432/oss_data_platform
```

## Tables

> **Note**: Table schemas are defined in `contracts/schemas/*.yml` - that's the source of truth.
> Use `from ingestion.dlt.contract_loader import get_dlt_columns` to load schemas programmatically.

### nba.teams
| Column | Type | Description |
|--------|------|-------------|
| team_id | INTEGER | Primary key |
| team_name | VARCHAR | Team name (e.g., "Lakers") |
| team_abbreviation | VARCHAR | 3-letter code (e.g., "LAL") |
| city | VARCHAR | City name |
| conference | VARCHAR | "East" or "West" |
| division | VARCHAR | Division name |
| is_active | BOOLEAN | Currently active |

### nba.players
| Column | Type | Description |
|--------|------|-------------|
| player_id | INTEGER | Primary key |
| player_name | VARCHAR | Full name |
| team_id | INTEGER | Current team |
| position | VARCHAR | Position (G, F, C) |
| height | VARCHAR | Height |
| weight | INTEGER | Weight |
| college | VARCHAR | College attended |
| country | VARCHAR | Country |
| draft_year | INTEGER | Draft year |
| career_pts | FLOAT | Career PPG |
| career_reb | FLOAT | Career RPG |
| career_ast | FLOAT | Career APG |

### nba.games
| Column | Type | Description |
|--------|------|-------------|
| game_id | VARCHAR | Primary key |
| game_date | DATE | Game date |
| season | VARCHAR | Season (e.g., "2025-26") |
| home_team_id | INTEGER | Home team |
| away_team_id | INTEGER | Away team |
| home_score | INTEGER | Home team score |
| away_score | INTEGER | Away team score |
| winner_team_id | INTEGER | Winning team |
| game_status | VARCHAR | Status text |
| venue | VARCHAR | Arena name |

### nba.betting_odds
| Column | Type | Description |
|--------|------|-------------|
| game_id | VARCHAR | Game reference |
| book_name | VARCHAR | Sportsbook name |
| market_type | VARCHAR | "2way", "spread", "total" |
| home_odds | FLOAT | Home team odds |
| away_odds | FLOAT | Away team odds |
| home_line | FLOAT | Spread line |
| total_line | FLOAT | Over/under line |
| captured_at | TIMESTAMP | When captured |

### nba.boxscores
| Column | Type | Description |
|--------|------|-------------|
| game_id | VARCHAR | Game reference |
| player_id | INTEGER | Player reference |
| team_id | INTEGER | Team reference |
| points | INTEGER | Points scored |
| assists | INTEGER | Assists |
| rebounds_total | INTEGER | Total rebounds |
| steals | INTEGER | Steals |
| blocks | INTEGER | Blocks |
| turnovers | INTEGER | Turnovers |
| field_goal_pct | FLOAT | FG% |
| three_point_pct | FLOAT | 3P% |
| plus_minus | INTEGER | +/- |
| minutes | VARCHAR | Minutes played |

### nba.team_boxscores
| Column | Type | Description |
|--------|------|-------------|
| game_id | VARCHAR | Game reference |
| team_id | INTEGER | Team reference |
| is_home | BOOLEAN | Home team flag |
| points | INTEGER | Total points |
| assists | INTEGER | Total assists |
| rebounds_total | INTEGER | Total rebounds |
| field_goal_pct | FLOAT | Team FG% |
| three_point_pct | FLOAT | Team 3P% |
| turnovers | INTEGER | Team turnovers |

## Useful Queries

### Team standings by win %
```sql
SELECT 
    t.team_name,
    COUNT(CASE WHEN g.winner_team_id = t.team_id THEN 1 END) as wins,
    COUNT(*) as games,
    ROUND(COUNT(CASE WHEN g.winner_team_id = t.team_id THEN 1 END)::numeric / COUNT(*) * 100, 1) as win_pct
FROM nba.teams t
JOIN nba.games g ON t.team_id IN (g.home_team_id, g.away_team_id)
WHERE g.game_status = 'Final'
GROUP BY t.team_id, t.team_name
ORDER BY win_pct DESC;
```

### Player averages
```sql
SELECT 
    player_name,
    ROUND(AVG(points), 1) as ppg,
    ROUND(AVG(rebounds_total), 1) as rpg,
    ROUND(AVG(assists), 1) as apg,
    COUNT(*) as games
FROM nba.boxscores
GROUP BY player_id, player_name
HAVING COUNT(*) >= 5
ORDER BY ppg DESC
LIMIT 20;
```

### Today's odds
```sql
SELECT 
    g.away_team_name || ' @ ' || g.home_team_name as matchup,
    o.market_type,
    o.home_odds,
    o.away_odds,
    o.total_line
FROM nba.betting_odds o
JOIN nba.todays_games g ON o.game_id = g.game_id
ORDER BY g.game_id, o.market_type;
```
