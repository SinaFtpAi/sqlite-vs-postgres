"""The engine order is a reproducible shuffle of each scenario, repeat and worker level."""

from __future__ import annotations

import random

from dbbench.order import EngineOrder

ENGINES = ("sqlite_wal_normal", "sqlite_wal_full", "postgres", "postgres_unix")


def test_same_seed_and_key_give_the_same_order() -> None:
    """Shuffling twice with the same seed and key gives the same list, which keeps every engine."""
    first = EngineOrder(7).shuffled(ENGINES, "single_insert", 0, 1)
    second = EngineOrder(7).shuffled(ENGINES, "single_insert", 0, 1)
    assert first == second
    assert sorted(first) == sorted(ENGINES)


def test_the_order_is_the_documented_shuffle() -> None:
    """The shuffle is random.Random seeded with the seed, scenario, repeat and worker level."""
    expected = list(ENGINES)
    random.Random("7:point_read:2:4").shuffle(expected)  # noqa: S311
    assert EngineOrder(7).shuffled(ENGINES, "point_read", 2, 4) == expected


def test_different_keys_give_different_orders() -> None:
    """Across repeats the engines do not always run in one order."""
    orders = {
        tuple(EngineOrder(7).shuffled(ENGINES, "point_read", repeat, 1)) for repeat in range(10)
    }
    assert len(orders) > 1


def test_the_input_is_not_changed() -> None:
    """The shuffle works on a copy: the engine tuple from the config stays as it is."""
    engines = list(ENGINES)
    EngineOrder(7).shuffled(engines, "point_read", 0, 1)
    assert engines == list(ENGINES)
