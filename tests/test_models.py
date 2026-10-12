"""Tests for models.py — input validation on EditRegion."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from mcp_bildsprache.models import EditRegion


def test_valid_region_defaults_to_edit():
    region = EditRegion(id="mug_1", box=[100, 200, 300, 400], desc="a mug")
    assert region.kind == "edit"
    assert region.to is None


@pytest.mark.parametrize(
    "box",
    [[0, 0, 1001, 10], [10, 10, 5, 20], [10, 10, 20, 10], [1, 2, 3], [-1, 0, 10, 10]],
)
def test_bad_boxes_are_rejected(box):
    with pytest.raises(ValidationError):
        EditRegion(id="x", box=box, desc="x")


def test_bad_id_is_rejected():
    with pytest.raises(ValidationError):
        EditRegion(id="Mug 1", box=[0, 0, 10, 10], desc="x")


def test_move_needs_to_and_only_move_takes_it():
    with pytest.raises(ValidationError, match="required for kind='move'"):
        EditRegion(id="dog", box=[0, 0, 10, 10], desc="dog", kind="move")
    with pytest.raises(ValidationError):
        EditRegion(id="dog", box=[0, 0, 10, 10], desc="dog", to=[20, 0, 30, 10])
    region = EditRegion(id="dog", box=[0, 0, 10, 10], desc="dog", kind="move", to=[20, 0, 30, 10])
    assert region.to == [20, 0, 30, 10]
