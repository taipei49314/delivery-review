"""Authored fixture tests. The standard producer runs these only in its guest."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from parcel import total_units


def test_empty_delivery():
    assert total_units([]) == 0


def test_multiple_items():
    assert total_units([1, 2, 4]) == 7


def test_negative_quantity_is_rejected():
    with pytest.raises(ValueError, match='nonnegative integers'):
        total_units([1, -2])


def test_boolean_quantity_is_rejected():
    with pytest.raises(ValueError, match='nonnegative integers'):
        total_units([True])
