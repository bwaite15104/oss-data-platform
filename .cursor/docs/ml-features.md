# ML Features for Betting Models

## Project Goal
Build ML models to predict NBA game outcomes for sports betting.

## Target Predictions
1. **Game Winner** - Which team wins (moneyline)
2. **Point Spread** - Will team cover the spread
3. **Over/Under** - Total points over/under line
4. **Player Props** - Individual player performance

## Available Data for Features

### Team-Level Features
From `nba.team_boxscores`:
- Points per game (offensive rating)
- Points allowed (defensive rating)
- Field goal percentage
- 3-point percentage
- Rebounds, assists, turnovers
- Home vs away splits

### Player-Level Features
From `nba.boxscores` and `nba.players`:
- Player efficiency metrics
- Minutes played trends
- Scoring consistency
- Position-specific stats
- Injury/availability impact

### Game Context Features
From `nba.games`:
- Home/away indicator
- Days of rest between games
- Back-to-back games
- Travel distance
- Time zone changes

### Market Features
From `nba.betting_odds`:
- Opening vs current odds
- Line movement direction
- Implied probability from odds
- Spread/total correlation

## Feature Engineering Ideas

### Rolling Averages
```sql
-- Last N games performance
AVG(points) OVER (PARTITION BY team_id ORDER BY game_date ROWS 5 PRECEDING)
```

### Head-to-Head
```sql
-- Historical matchup results
SELECT winner_team_id, COUNT(*) 
FROM nba.games 
WHERE (home_team_id = X AND away_team_id = Y) 
   OR (home_team_id = Y AND away_team_id = X)
GROUP BY winner_team_id
```

### Rest Days
```sql
-- Days since last game
game_date - LAG(game_date) OVER (PARTITION BY team_id ORDER BY game_date)
```

### Pace/Style Metrics
```sql
-- Possessions estimate
0.5 * ((fga + 0.4 * fta - 1.07 * (oreb / (oreb + dreb)) * (fga - fgm) + tov))
```

## Model Training Pipeline (Future)
1. Feature store in PostgreSQL
2. SQLMesh transforms for feature engineering
3. Python ML training scripts
4. Model serving for predictions
5. Comparison vs betting lines
