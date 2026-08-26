"""Tests for the accuracy scorer's own comparator.

The scorer produces the headline "97%" number, so a bug in it is worse than a
bug in an ordinary module: it silently changes what the project believes
about itself. Both cases below were real, and both failed in the same
direction -- deflating accuracy -- which is the direction least likely to be
questioned and most likely to send someone chasing an extraction problem
that does not exist.
"""

from __future__ import annotations

import pytest

from tools.score_accuracy import normalize, values_match


# --- a numeric zero is not a boolean false ----------------------------------

def test_zero_teaching_years_is_a_number_not_a_boolean():
    """Real bug: "0" sat in the boolean set, so a correctly extracted
    "0 teaching years" normalised to "false" while the export's "0.0"
    normalised to "0" -- a mismatch on a right answer."""
    assert values_match("0", "0.0")
    assert values_match("0.0", "0")
    assert normalize("0") == normalize("0.0")


def test_zero_publications_matches_zero_publications():
    assert values_match("0", "0")
    assert values_match("0", "0.00")


def test_real_booleans_still_compare_correctly():
    assert values_match("TRUE", "true")
    assert values_match("FALSE", "false")
    assert not values_match("TRUE", "FALSE")


def test_zero_does_not_match_false():
    """The two were conflated before; they are different kinds of answer and
    should not silently satisfy each other."""
    assert not values_match("0", "FALSE")


# --- partial dates compare at the coarser precision -------------------------

def test_a_bare_year_matches_a_full_date_for_the_same_year():
    """Real bug: the extractor stopped fabricating "2023-01-01" for a resume
    that only said "2023", but answer keys recorded before that change still
    hold the fabricated form. Scoring them as different penalised the fix."""
    assert values_match("2023-01-01", "2023")
    assert values_match("2023", "2023-01-01")


def test_a_year_month_matches_a_full_date_in_that_month():
    assert values_match("2021-05-01", "2021-05")
    assert values_match("2021-05", "2021-05-01")


def test_different_years_still_mismatch():
    assert not values_match("2023-01-01", "2024")
    assert not values_match("2023", "2024-01-01")


def test_different_months_in_the_same_year_still_mismatch():
    assert not values_match("2021-05", "2021-06-01")


def test_date_leniency_does_not_leak_into_ordinary_numbers():
    """2023 vs 2024 as *numbers* must not be rescued by date logic."""
    assert not values_match("2023", "2024")
    assert not values_match("7", "70")


# --- formatting differences that were never errors --------------------------

@pytest.mark.parametrize(
    "expected,got",
    [
        ("8", "8.0"),
        ("76.6", "76.60"),
        ("2015-06-01", "2015-06-01 00:00:00"),
        ("NET", "net"),
        (" PG ", "PG"),
    ],
)
def test_formatting_differences_are_not_counted_as_errors(expected, got):
    assert values_match(expected, got)


def test_blank_matches_blank_but_not_a_value():
    assert values_match("", "")
    assert not values_match("", "7")
    assert not values_match("7", "")
