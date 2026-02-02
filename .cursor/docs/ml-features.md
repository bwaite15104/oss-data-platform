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
| `game_features` | **594** | ML training data with rolling averages + star player returns |
| `team_features` | 31 | Season-level team stats |
| `team_injury_features` | 29 | Injury impact by team |
| `feature_registry` | 11 | Registered features metadata |

**New Features (Jan 2026):**
- **Star Player Return Features** (16 features):
  - Detects when star players (20+ PPG) or key players (15-20 PPG) return after 7+ day absences
  - Tracks extended returns (14+ days) with weighted impact scores
  - Includes home/away counts, impact scores, days since return, and differential features

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

## ML Pipeline Architecture

**Training and Prediction pipelines are separated in Dagster:**

```
features_dev.game_features
    ↓
train_game_winner_model (Dagster asset, daily schedule)
    ↓
ml_dev.model_registry (stores model metadata)
    ↓
generate_game_predictions (Dagster asset, eager - runs when model updates)
    ↓
ml_dev.predictions (stores predictions for upcoming games)
```

### Current Implementation

✅ **Training Asset**: `orchestration/dagster/assets/ml/training.py`
- Asset: `train_game_winner_model`
- Schedule: `AutomationCondition.on_cron("@daily")` - Retrain daily
- Process: Loads features → Trains XGBoost → Saves model → Registers in `ml_dev.model_registry`

✅ **Prediction Asset**: `orchestration/dagster/assets/ml/predictions.py`
- Asset: `generate_game_predictions`
- Schedule: `AutomationCondition.eager()` - Runs when model updates
- Process: Loads latest model → Generates predictions → Stores in `ml_dev.predictions`

### Model and Feature Source of Truth (for LLM)

- **Model history, which features were used per run, and metrics** → **MLflow** (experiment `nba_game_winner_prediction`): runs, params `features_part*` / `top_features`, `feature_count`, `algorithm`, metrics (`test_accuracy`, `train_accuracy`), artifacts `training_artifacts/features.json`, `training_artifacts/feature_importances.json`, Model Registry `game_winner_model`.
- **Current feature definitions and schema** → **Feast** (`feature_repo/features.py`, `feature_repo/data_sources.py`); `feast apply` and Feast UI http://localhost:8080.

For how the LLM should use these when doing model improvements, evaluation, or historical review → **`.cursor/docs/mlflow-and-feast-context.md`**.

## ✅ Completed Steps

1. ✅ **Feature store created** - `features_dev.game_features` with 584 games
2. ✅ **ML schema infrastructure** - `ml_dev.model_registry`, `ml_dev.predictions`, `ml_dev.betting_results`
3. ✅ **Training pipeline** - Dagster asset for XGBoost model training
4. ✅ **Prediction pipeline** - Dagster asset for generating predictions
5. ✅ **Model versioning** - Automatic versioning and metadata tracking

## 🚧 Next Immediate Steps (Phase 1: ML Pipeline)

1. [x] **Validate ML assets** - Run training asset, verify model saved and registered ✅
2. [x] **Test predictions** - Generate predictions for upcoming games ✅
3. [x] **Model evaluation** - Track accuracy over time, compare predictions vs. actuals ✅
4. [ ] **Feature expansion** - Add more features (rest days, advanced stats, head-to-head) - **HIGH PRIORITY**
5. [x] **Hyperparameter tuning** - Attempted (2026-01-25): minimal improvement (0.6552 → 0.6559), need more impactful changes ✅

**Recent Model Improvements (2026-01-25):**
- **Iteration 15 - Team Consistency/Variance Features**: Added team consistency/variance features to capture performance stability
  - Features: `rolling_5_ppg_stddev`, `rolling_5_opp_ppg_stddev`, `rolling_5_ppg_cv`, `rolling_5_opp_ppg_cv` (and 10-game versions) for home/away teams
  - Differential features: `ppg_stddev_diff_5`, `opp_ppg_stddev_diff_5`, `ppg_cv_diff_5`, `opp_ppg_cv_diff_5` (and 10-game versions)
  - Rationale: Teams with lower variance (more consistent performance) are more predictable
  - Coefficient of variation (stddev/mean) normalizes variance by team quality
  - Results: Training in progress - check MLflow for results
  - Status: Code complete, SQLMesh plan executed, training in progress
- **Iteration 12 - Clutch Performance Features**: Added clutch performance features to capture team performance in close games (decided by <= 5 points)
  - Features: `home_clutch_win_pct`, `away_clutch_win_pct`, `home_clutch_game_count`, `away_clutch_game_count`, `home_clutch_avg_point_diff`, `away_clutch_avg_point_diff`, `clutch_win_pct_diff`, `clutch_avg_point_diff_diff`
  - Results: Training in progress - check MLflow for results
  - Status: Code complete, SQLMesh plan executed, training in progress
- **Iteration 9 - Rolling Schedule Strength (SOS) Features**: Added rolling schedule strength features using rolling opponent win_pct to contextualize team performance (5-game, 10-game, weighted SOS, SOS-adjusted win percentages)
  - Features: `home_sos_5_rolling`, `away_sos_5_rolling`, `home_sos_10_rolling`, `away_sos_10_rolling`, `home_sos_5_weighted`, `away_sos_5_weighted`, `sos_5_diff`, `sos_10_diff`, `sos_5_weighted_diff`, `home_win_pct_vs_sos_10`, `away_win_pct_vs_sos_10`, `win_pct_vs_sos_10_diff`
  - Results: Training in progress - check MLflow for results
  - Status: Code complete, training in progress
- **Iteration 7 - Recent Head-to-Head Performance Features**: Added recent H2H win percentages (last 3, last 5), point differentials, and momentum score
  - Features: `home_h2h_win_pct_3`, `home_h2h_win_pct_5`, `home_h2h_avg_point_diff_3`, `home_h2h_avg_point_diff_5`, `home_h2h_momentum`
  - Results: Training in progress - check MLflow for results
  - Status: Code complete, SQLMesh plan executed, training in progress
- **Iteration 6 - Increased Regularization**: Increased regularization to reduce overfitting
  - Increased `reg_alpha` from 0.3 to 0.5, `reg_lambda` from 1.5 to 2.0, `min_child_samples` to 30
  - Results: Test accuracy 0.6442, train accuracy 0.7484, train-test gap 0.1042 (reduced overfitting)
  - Status: Overfitting reduced, but test accuracy still below target (0.75)
- **Iteration 4 - Feature Selection**: Implemented automatic feature selection to remove low-importance features
  - Added `feature_selection_enabled` and `feature_selection_threshold` config parameters
  - Uses `SelectFromModel` with quick model to identify important features
  - Results: 67 features selected (removed 33), test accuracy 0.6405, reduced overfitting
  - Status: Code complete, threshold may need tuning
- **Iteration 3 - Away Team Upset Tendency**: Added historical upset rate features for away teams
  - Features: `away_upset_tendency_season`, `away_upset_tendency_rolling`, sample size indicators
  - Results: Test accuracy 0.6438, features may need full materialization
- **Iteration 2 - Feature Interactions**: Added 6 interaction features (injury×rest, net_rtg×home, etc.)
  - Results: Test accuracy 0.6456, interaction features may need materialization
- **Iteration 1 - Advanced Stats Features**: Added eFG%, TS%, Pace, OffRtg, DefRtg, NetRtg with rolling 5/10-game averages
  - Updated: `int_team_rolling_stats`, `mart_game_features`, `features_dev.game_features`
  - Added ~18 new features (9 per team × 2 time windows) + differentials
  - Results: Test accuracy 0.6559 (best so far)
- **Previous**: Hyperparameter tuning (0.6552 → 0.6559) - minimal improvement, confirmed need for feature engineering

## 🔮 Future ML Enhancements (Phase 2: Expand ML Pipeline)

### Multiple Models
- [ ] **Spread prediction model** - Predict point spread outcomes (requires historical odds)
- [ ] **Over/Under model** - Predict total points over/under (requires historical odds)
- [ ] **Player prop models** - Individual player performance predictions

### Model Evaluation & Comparison
- [x] **Model evaluation asset** - Compare predictions vs. actual outcomes, calculate metrics ✅ COMPLETE
- [ ] **A/B testing** - Compare different algorithms (XGBoost vs. Neural Networks vs. Logistic Regression)
- [ ] **Feature importance analysis** - Understand which features drive predictions (SHAP values)
- [x] **Backtesting framework** - Historical performance simulation ✅ COMPLETE (via prediction_date_cutoff parameter)

### Advanced MLOps
- [ ] **Hyperparameter tuning** - Automated optimization using Optuna/Hyperopt
- [ ] **Model monitoring** - Track prediction drift, feature drift over time
- [ ] **Automated retraining triggers** - Retrain when accuracy drops below threshold
- [ ] **Model explainability** - SHAP values, feature contributions per prediction

## 📊 Observability & Dashboards (Phase 4)

### Metabase Dashboards
- [ ] **Model Performance Dashboard** - Track accuracy, precision, recall, F1 over time from `ml_dev.model_evaluations`
- [ ] **Prediction Analysis Dashboard** - Predictions vs actuals, confidence distributions, upset analysis
- [ ] **Feature Quality Dashboard** - Feature completeness, data freshness, missing values
- [ ] **Data Ingestion Dashboard** - Ingestion status, row counts, last update times
- [ ] **Betting Results Dashboard** - P&L tracking, win rates by confidence level, ROI analysis

### Setup
1. Connect Metabase to `nba_analytics` database (PostgreSQL)
2. Create dashboards for key metrics from `ml_dev.model_evaluations`, `ml_dev.predictions`
3. Set up scheduled reports/alerts for model performance degradation
4. Access: http://localhost:3002

**Reference**: See `.cursorrules` Phase 4 section for detailed dashboard requirements.

## 📊 Data Quality Integration (Phase 3: Quality Assurance)

### Baselinr Integration
- [x] **Regenerate Baselinr config** (opt iter 17) - `configs/generated/baselinr/baselinr_config.yml` uses database `nba_analytics`, profiling tables `raw_dev.games`, `raw_dev.players`, `raw_dev.teams`, `raw_dev.boxscores`, `raw_dev.team_boxscores`; adapter default POSTGRES_DB set to nba_analytics; ODCS datasets updated to raw_dev.
- [x] **Wire quality assets** - Quality assets load when config exists; definitions.py wires quality module; quality/__init__.py resolves config path (Docker /app or project-relative).
- [ ] **Quality checks on features** - Monitor `features_dev.*` tables for drift
- [ ] **Quality checks on predictions** - Validate prediction confidence, feature completeness

### Data Validation
- [ ] **Schema validation** - Ensure features match training schema
- [ ] **Data freshness checks** - Alert when features are stale
- [ ] **Missing feature detection** - Identify games with incomplete features

## Interaction feature learnings (game-winner, iter 118–120)

- **win_pct_diff_10 × context (rest_advantage, home_advantage):** Iterations 118 and 119 added `win_pct_diff_10_x_rest_advantage` and `win_pct_diff_10_x_home_advantage`; both regressed test accuracy. Reverting `win_pct_diff_10_x_home_advantage` (iter 120) restored baseline and improved to 0.6446. Existing `form_divergence_diff_x_home_advantage` and `net_rtg_diff_10_x_home_advantage` likely capture home/context signal; avoid further win_pct_diff_10 × context interactions. Prefer other interaction types (e.g. momentum × home_advantage, fg_pct × rest_advantage) or validation/split/SQLMesh levers.

## 📋 Remaining Data Gaps (Free Sources Only)

1. [ ] **Historical betting odds** - Building daily via `nba_betting_odds` asset (long-term)
2. [ ] **Advanced stats** - Calculate from existing data (Pace, OffRtg, DefRtg, eFG%, TS%)
3. [ ] **Rest/travel data** - Calculate from schedule (back-to-backs, travel distance)
