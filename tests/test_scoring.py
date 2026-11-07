from fractions import Fraction

import pytest

from app.services.scoring import booking_value, compute_trust


def test_proposal_illustration_20_ratings_averaging_4_7():
    summary = compute_trust([Fraction(47, 10)] * 20)
    assert summary.adjusted_rating == 4.46
    assert summary.trust_score == 89
    assert summary.is_new is False


def test_single_five_star_is_new_and_shows_no_perfect_figures():
    summary = compute_trust([Fraction(5)])
    assert summary.label == "New · 1 rating"
    assert summary.is_new is True
    assert summary.trust_score is None
    assert summary.average_stars is None


@pytest.mark.parametrize(("count", "label"), [(0, "New · 0 ratings"), (2, "New · 2 ratings")])
def test_below_three_rated_bookings_is_new(count, label):
    summary = compute_trust([Fraction(4)] * count)
    assert summary.is_new is True
    assert summary.label == label


def test_three_rated_bookings_earns_a_score():
    summary = compute_trust([Fraction(7, 2)] * 3)
    assert summary.is_new is False
    assert summary.trust_score == 70
    assert summary.label == "70 · 3 ratings"


def test_two_raters_on_one_booking_count_as_one_delivery():
    assert booking_value([5, 4]) == Fraction(9, 2)
    assert compute_trust([booking_value([5, 4])]).rated_bookings == 1


def test_lowest_and_highest_scores_stay_in_range():
    assert 0 <= compute_trust([Fraction(1)] * 500).trust_score <= 100
    assert compute_trust([Fraction(5)] * 5000).trust_score == 100


def test_exact_half_rounds_up_not_to_even():
    # (x + 17.5) / (n + 5) * 20 = 72.5 with three bookings summing to 11.5
    values = [Fraction(4), Fraction(4), Fraction(7, 2)]
    assert compute_trust(values).trust_score == 73
