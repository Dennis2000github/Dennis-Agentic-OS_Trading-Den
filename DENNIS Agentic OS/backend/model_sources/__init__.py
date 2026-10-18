"""CLI-native model sources.

Each agent has its own module with a defensive parser for that CLI's model
output. ``registry.get_source`` returns the implementation for an agent id.
The Model Registry is intentionally *not* consulted anywhere in this package.
"""

from .base import ModelListError, ModelSource
from .registry import get_source, list_models, validate_model, clear_cache

__all__ = [
    "ModelListError",
    "ModelSource",
    "get_source",
    "list_models",
    "validate_model",
    "clear_cache",
]
