"""The generator must give the same rows for the same seed, in a fixed draw order."""

from __future__ import annotations

from datetime import timedelta

from dbbench.config import ConfigLoader
from dbbench.dataset import Order, OrderGenerator

FIRST_THREE_ROWS = (
    (
        83811,
        7396,
        "paid",
        "2025-04-17T19:27:32+00:00",
        (
            "gdctopacgqptslmhxuelhfuckjjhbmdychqxjfbru xxkmwejsssbrogmhys"
            "fiubwvkbyptfnxrdduoulpj dnuxeesqkqmgozsd"
        ),
    ),
    (
        70382,
        49309,
        "paid",
        "2025-09-06T22:11:49+00:00",
        (
            "jheatt uncqwnfkezyyqndj vggpctw odrzeoq zustkskymgbaopatbbai"
            "nhnotxpgmkcrjlxbrrbtvcfodemxcvxcroacugkm"
        ),
    ),
    (
        52566,
        3942,
        "paid",
        "2025-01-01T20:05:22+00:00",
        (
            " hvmlz ptei poubpnxezcfqsgdygqqlpozftgksiiubm  bfhzxxjewsq r"
            "awirzddcohqtfrhnywclhaurhuolacxyowpddiyv"
        ),
    ),
)


def _as_tuple(order: Order) -> tuple[int, int, str, str, str]:
    """Returns the fields of an order in the snapshot's layout."""
    return (
        order.user_id,
        order.amount_cents,
        order.status,
        order.created_at.isoformat(),
        order.note,
    )


def test_first_three_rows_match_the_snapshot() -> None:
    """The rows for seed 42 are fixed, so every engine and every run gets the same data."""
    dataset = ConfigLoader().load().dataset
    generator = OrderGenerator(dataset, seed=dataset.seed)
    rows = tuple(_as_tuple(generator.next_order()) for _ in range(3))
    assert rows == FIRST_THREE_ROWS


def test_same_seed_gives_the_same_rows() -> None:
    """Two generators with one seed produce identical streams."""
    dataset = ConfigLoader().load().dataset
    first = OrderGenerator(dataset, seed=7)
    second = OrderGenerator(dataset, seed=7)
    assert [first.next_order() for _ in range(50)] == [second.next_order() for _ in range(50)]


def test_different_seed_gives_different_rows() -> None:
    """A different seed gives a different stream."""
    dataset = ConfigLoader().load().dataset
    assert (
        OrderGenerator(dataset, seed=1).next_order() != OrderGenerator(dataset, seed=2).next_order()
    )


def test_values_stay_inside_the_configured_ranges() -> None:
    """Every field respects the users, amount, status and time ranges of the config."""
    dataset = ConfigLoader().load().dataset
    generator = OrderGenerator(dataset, seed=dataset.seed)
    for _ in range(500):
        order = generator.next_order()
        assert 1 <= order.user_id <= dataset.users
        assert dataset.amount_cents.minimum <= order.amount_cents <= dataset.amount_cents.maximum
        assert order.status in dataset.statuses
        end = dataset.start + timedelta(days=dataset.span_days)
        assert dataset.start <= order.created_at < end
        assert len(order.note) == dataset.note_length
