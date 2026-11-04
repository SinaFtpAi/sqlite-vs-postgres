"""Deterministic order rows, identical for every engine.

The random draws happen in a fixed order (user, amount, status, time, note), so one seed
always gives the same rows on SQLite and on PostgreSQL.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from dbbench.config import DatasetSettings

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dbbench.config import DatasetSettings

SECONDS_PER_DAY = 86_400


@dataclass(frozen=True, slots=True)
class Order:
    """One order row. The id is not part of it: the caller assigns ids."""

    user_id: int
    amount_cents: int
    status: str
    created_at: datetime
    note: str


class OrderGenerator:
    """Produces order rows one at a time from a single seeded random stream."""

    def __init__(self, dataset: DatasetSettings, seed: int) -> None:
        """Seeds the stream and remembers the value ranges from the config."""
        self._dataset = dataset
        self._rng = random.Random(seed)  # noqa: S311
        self._statuses = tuple(dataset.statuses)
        self._weights = tuple(dataset.statuses.values())

    def next_order(self) -> Order:
        """Returns the next row, drawing its random values in the fixed order."""
        user_id = self._rng.randint(1, self._dataset.users)
        amount = self._dataset.amount_cents
        amount_cents = self._rng.randint(amount.minimum, amount.maximum)
        status = self._rng.choices(self._statuses, weights=self._weights)[0]
        offset = self._rng.randint(0, self._dataset.span_days * SECONDS_PER_DAY - 1)
        created_at = self._dataset.start + timedelta(seconds=offset)
        note = "".join(self._rng.choices(self._dataset.note_alphabet, k=self._dataset.note_length))
        return Order(
            user_id=user_id,
            amount_cents=amount_cents,
            status=status,
            created_at=created_at,
            note=note,
        )
