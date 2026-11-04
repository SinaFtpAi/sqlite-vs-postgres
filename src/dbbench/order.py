"""The engine order of each measured run, shuffled so that no engine always goes first."""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


class EngineOrder:
    """Shuffles the engines of one scenario. The same seed and names always give the same order."""

    def __init__(self, seed: int) -> None:
        """Remembers the seed from the config."""
        self._seed = seed

    def shuffled(
        self, engines: Sequence[str], scenario: str, repeat: int, workers: int
    ) -> list[str]:
        """Returns the engines in this run's order, one shuffle per scenario, repeat and level."""
        names = list(engines)
        # A reproducible shuffle, not a secret, so a seeded generator is the right tool.
        stream = random.Random(f"{self._seed}:{scenario}:{repeat}:{workers}")  # noqa: S311
        stream.shuffle(names)
        return names
