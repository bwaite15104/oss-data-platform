"""Feast entity definitions for NBA analytics platform."""

from feast import Entity, ValueType

# Game entity - primary identifier for predictions
game = Entity(
    name="game",
    description="NBA game identifier",
    value_type=ValueType.STRING,
)

# Team entity - for team-level features
team = Entity(
    name="team",
    description="NBA team identifier",
    value_type=ValueType.INT64,
)
