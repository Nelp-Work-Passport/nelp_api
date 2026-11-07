"""The trust score. Pure functions only, with no database and no language model.

Proposal section 5:
    adjusted rating = (sum of booking ratings + 5 x 3.5) / (rated bookings + 5)
    trust score     = round(adjusted rating x 20)
Riders with fewer than 3 rated bookings are "New" and show no score.
Exact fractions are used so that the same inputs always give the same score.
"""

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from fractions import Fraction

PRIOR_WEIGHT = 5
PRIOR_MEAN = Fraction(7, 2)
MIN_RATED_BOOKINGS = 3


@dataclass(frozen=True)
class TrustSummary:
    rated_bookings: int
    is_new: bool
    trust_score: int | None
    adjusted_rating: float | None
    average_stars: float | None
    label: str


def booking_value(stars: Sequence[int]) -> Fraction:
    """One delivery counts once, even when both merchant and recipient rate it."""
    if not stars:
        raise ValueError("a rated booking needs at least one rating")
    return Fraction(sum(stars), len(stars))


def _ratings_word(count: int) -> str:
    return f"{count} rating" if count == 1 else f"{count} ratings"


def compute_trust(booking_values: Iterable[Fraction]) -> TrustSummary:
    values = list(booking_values)
    count = len(values)
    total = sum(values, Fraction(0))

    if count < MIN_RATED_BOOKINGS:  # the average is withheld too: 1 review of 5 stars misleads
        return TrustSummary(
            rated_bookings=count,
            is_new=True,
            trust_score=None,
            adjusted_rating=None,
            average_stars=None,
            label=f"New · {_ratings_word(count)}",
        )

    adjusted = (total + PRIOR_WEIGHT * PRIOR_MEAN) / (count + PRIOR_WEIGHT)
    score = math.floor(adjusted * 20 + Fraction(1, 2))  # round half up, not half to even
    return TrustSummary(
        rated_bookings=count,
        is_new=False,
        trust_score=score,
        adjusted_rating=float(round(adjusted, 2)),
        average_stars=float(round(total / count, 2)),
        label=f"{score} · {_ratings_word(count)}",
    )
