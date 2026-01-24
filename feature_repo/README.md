# Feast Feature Repository – NBA Analytics

Feature store definitions for the NBA game-winner prediction pipeline.

## Structure

- **`feature_store.yaml`** – Project config, registry path, online store (Postgres).
- **`entities.py`** – Entities (`game`, `team`).
- **`data_sources.py`** – `PostgreSQLSource` for game features (marts + momentum).
- **`features.py`** – `FeatureView` `game_features` and schema.

## Apply and UI

```bash
# From feature_repo/
feast apply          # Deploy definitions to registry
feast ui             # Start Feast UI (http://localhost:8080)
```

Docker: `feast_ui` runs `feast apply && feast ui` on startup.

## Editing features

1. Edit `features.py`, `data_sources.py`, or `entities.py`.
2. Run **`feast apply`** so the registry and UI reflect changes.
3. Restart Feast UI (or run `feast apply` in the repo) so the UI shows the new state.

## Versioning

**Feast does not store a history of feature definitions.** The registry holds the **current** applied state only. When you `feast apply` after edits, that state is updated; previous definitions are not kept.

**How to track versions:**

1. **Git** – Commit feature repo changes (e.g. `features.py`, `data_sources.py`). Use Git history as your version history. Tag releases if you like (e.g. `v1.0`, `v1.1`).
2. **Tags on the FeatureView** – We use `tags={"version": "v1.0"}`. Bump the `version` tag when you make meaningful changes (e.g. new fields, breaking changes) and keep it in sync with Git tags or releases.
3. **MLflow** – Training logs `features.json` per run. Use MLflow runs + artifacts to see which feature set was used for each model.

**Suggested workflow:**

- Use **Git** for full history of feature definitions.
- Use **Feast tags** (`version`) to mark notable feature-view versions.
- Use **MLflow** to record which feature set (and run) was used for each trained model.

## Registry

- Path: `data/registry.db` (SQLite, local).
- Persisted via `feature_repo` volume mount in Docker.
