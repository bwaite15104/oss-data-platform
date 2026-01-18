"""ML pipeline assets for training and predictions."""

from .training import train_game_winner_model
from .predictions import generate_game_predictions

__all__ = [
    "train_game_winner_model",
    "generate_game_predictions",
]
