from __future__ import annotations

import pytest

from app.api.photo_ids import normalize_external_photo_ids


def test_missing_photos_ids_remains_backward_compatible() -> None:
    assert normalize_external_photo_ids(None, expected_count=2) == [None, None]


def test_photos_ids_are_trimmed_and_preserve_order() -> None:
    assert normalize_external_photo_ids(
        [" COMPANY_002 ", "COMPANY_001"],
        expected_count=2,
    ) == ["COMPANY_002", "COMPANY_001"]


@pytest.mark.parametrize(
    ("values", "message"),
    [
        (["ONLY_ONE"], "数量必须与 photos 数量一致"),
        (["A", ""], "不能为空"),
        (["A", "A"], "不能重复"),
        (["A" * 129, "B"], "最多 128 个字符"),
    ],
)
def test_invalid_photos_ids_are_rejected(values: list[str], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        normalize_external_photo_ids(values, expected_count=2)
