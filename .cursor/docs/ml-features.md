# ML Features for Betting Models

## Project Goal
Build ML models to predict NBA game outcomes for sports betting.

## Target Predictions
1. **Game Winner** - Which team wins (moneyline)
2. **Point Spread** - Will team cover the spread
3. **Over/Under** - Total points over/under line
4. **Player Props** - Individual player performance

---

## Current Data Status

### ✅ Available Data (raw_dev schema)

| Table | Records | Description |
|-------|---------|-------------|
| `teams` | 34 | All NBA teams |
| `players` | 527 | Full player roster |
| `games` | 1,306 | Full season schedule |
| `boxscores` | **24,468** | Player game stats (ALL 689 completed games) |
| `team_boxscores` | **1,378** | Team game stats (ALL 689 completed games) |
| `injuries` | **118** | Current injury report (ESPN scrape) |
| `betting_odds` | 280 | Today's odds only |
| `todays_games` | 9 | Live scoreboard |

### ✅ Transformation Layer

| Schema | Table/View | Records | Description |
|--------|------------|---------|-------------|
| staging | `stg_*` | 5 views | Cleaned raw data |
| intermediate | `int_team_season_stats` | 31 | Season aggregates by team |
| intermediate | `int_team_rolling_stats` | 1,219 | Rolling 5/10 game stats |
| marts | `mart_game_features` | **584** | ML-ready features per game |
| marts | `mart_team_standings` | 31 | Current standings with streaks |

### ✅ Feature Store (features_dev schema)

| Table | Records | Description |
|-------|---------|-------------|
| `game_features` | **584** | ML training data with rolling averages |
| `team_features` | 31 | Season-level team stats |
| `team_injury_features` | 29 | Injury impact by team |
| `feature_registry` | 11 | Registered features metadata |

### ⚠️ Remaining Data Gaps

| Gap | Impact | Solution |
|-----|--------|----------|
| **Historical betting odds** | Can't train spread/O-U models | Need external API (odds-api.com) - building history daily |
| **Advanced stats** | No pace, efficiency ratings | Add to transformation layer |
| **Rest/travel data** | Back-to-backs, travel distance | Calculate from schedule |

---

## Available Features by Source

### Team-Level Features
From `raw_dev.team_boxscores`:
- Points per game (offensive rating)
- Points allowed (defensive rating)
- Field goal percentage / 3-point %
- Rebounds, assists, turnovers
- Home vs away splits

### Player-Level Features
From `raw_dev.boxscores` and `raw_dev.players`:
- Points, assists, rebounds per game
- Minutes played trends
- Scoring consistency (std dev)
- Position-specific stats
- Usage patterns

### Game Context Features
From `raw_dev.games`:
- Home/away indicator
- Game date/time
- Venue information
- Season type (regular/playoffs)

### Market Features (Today Only)
From `raw_dev.betting_odds`:
- Moneyline odds
- Spread (when available)
- Over/under total
- Implied probability

---

## Feature Engineering Ideas

### Rolling Averages (staging → features)
```sql
-- Last N games performance
SELECT 
    team_id,
    game_date,
    AVG(points) OVER (PARTITION BY team_id ORDER BY game_date ROWS 5 PRECEDING) as pts_last_5,
    AVG(points) OVER (PARTITION BY team_id ORDER BY game_date ROWS 10 PRECEDING) as pts_last_10
FROM staging_dev.stg_team_boxscores
```

### Rest Days
```sql
-- Days since last game
SELECT 
    team_id,
    game_date,
    game_date - LAG(game_date) OVER (PARTITION BY team_id ORDER BY game_date) as rest_days,
    CASE WHEN game_date - LAG(game_date) OVER (PARTITION BY team_id ORDER BY game_date) = 1 
         THEN true ELSE false END as is_back_to_back
FROM staging_dev.stg_games
```

### Head-to-Head History
```sql
-- Historical matchup results
SELECT 
    home_team_id,
    away_team_id,
    COUNT(*) as games_played,
    SUM(CASE WHEN winner_team_id = home_team_id THEN 1 ELSE 0 END) as home_wins
FROM staging_dev.stg_games 
WHERE game_status = 'Final'
GROUP BY home_team_id, away_team_id
```

### Implied Probability from Odds
```sql
-- Convert American odds to probability
SELECT 
    game_id,
    CASE 
        WHEN home_odds < 0 THEN ABS(home_odds) / (ABS(home_odds) + 100.0)
        ELSE 100.0 / (home_odds + 100.0)
    END as home_implied_prob
FROM raw_dev.betting_odds
WHERE market_type = '2way'
```

---

## Free Data Sources to Add

> **CONSTRAINT**: All data sources must be FREE. No paid APIs.

### 1. Historical Odds - ⚠️ LIMITED FREE OPTIONS
| Source | Free? | Notes |
|--------|-------|-------|
| The Odds API | Free tier: 500 req/mo | **Today's odds only**, no historical |
| Kaggle datasets | ✅ Free | Search for "NBA betting odds" - may be outdated |
| Sports-reference | Scraping only | Against TOS, not recommended |
| **Best option**: Collect daily | ✅ Free | Run `nba_betting_odds` daily, build history over time |

**Workaround**: We can't get historical odds for free. Instead:
1. Start collecting daily odds NOW
2. Build our own historical dataset over time
3. For initial training, use game outcomes without spread predictions

### 2. Injury Data - FREE OPTIONS
| Source | Method | Notes |
|--------|--------|-------|
| ESPN Injury Report | Scrape | `espn.com/nba/injuries` |
| CBS Sports | Scrape | `cbssports.com/nba/injuries` |
| RotoWire | Scrape | `rotowire.com/basketball/nba-lineups.php` |
| **Official NBA** | API | `/static/json/liveData/playbyplay/` may have status |

**Recommended**: Create scraper for ESPN injury page (free, reliable)

### 3. Advanced Stats - FREE (Calculate from existing data)
| Stat | Formula | Source Data |
|------|---------|-------------|
| Pace | `48 * (possessions / minutes)` | `team_boxscores` |
| OffRtg | `100 * (points / possessions)` | `team_boxscores` |
| DefRtg | `100 * (opp_points / possessions)` | `team_boxscores` |
| NetRtg | `OffRtg - DefRtg` | Calculated |
| eFG% | `(FGM + 0.5*3PM) / FGA` | `boxscores` |
| TS% | `PTS / (2 * (FGA + 0.44*FTA))` | `boxscores` |

**No API needed** - calculate in SQLMesh transformation layer

### 4. Additional Free NBA APIs
| API | Data | Notes |
|-----|------|-------|
| `nba_api` (Python) | Official stats | Rate limited, may block |
| NBA CDN (current) | ✅ Live data | Reliable, what we use |
| balldontlie.io | Basic stats | Free tier: 60 req/hr |

---

## Model Training Pipeline

```
raw_dev (dlt)
    ↓
staging_dev (SQLMesh - clean/validate)
    ↓
features_dev (SQLMesh - feature engineering)
    ↓
ml_dev.training_datasets (versioned snapshots)
    ↓
Python ML Training (scikit-learn, XGBoost, etc.)
    ↓
ml_dev.model_registry (model metadata)
    ↓
ml_dev.predictions (live predictions)
    ↓
ml_dev.betting_results (P&L tracking)
```

## Next Steps (Free Only)
1. [ ] **Backfill all boxscores** - Increase limit to get all ~689 completed games
2. [ ] **Start daily odds collection** - Schedule `nba_betting_odds` to run daily
3. [ ] **Add injury scraper** - ESPN injury report scraper (free)
4. [ ] **Build SQLMesh transformations** - Calculate advanced stats from existing data
5. [ ] **Create feature store tables** - Rolling averages, rest days, etc.
6. [ ] **Train baseline model** - Start with game winner prediction (no spread)
7. [ ] **Add spread predictions later** - Once we have enough historical odds data
