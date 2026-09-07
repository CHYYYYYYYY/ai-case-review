"""客户照片 ID 的校验与规范化。"""
from __future__ import annotations


MAX_EXTERNAL_PHOTO_ID_LENGTH = 128


def normalize_external_photo_ids(
    values: list[str] | None,
    *,
    expected_count: int,
) -> list[str | None]:
    """校验 photosIds；缺失时为旧客户端返回等长的 null 列表。"""
    if values is None:
        return [None] * expected_count
    if len(values) != expected_count:
        raise ValueError("photosIds 数量必须与 photos 数量一致")

    normalized = [value.strip() for value in values]
    if any(not value for value in normalized):
        raise ValueError("photosIds 不能为空")
    if any(len(value) > MAX_EXTERNAL_PHOTO_ID_LENGTH for value in normalized):
        raise ValueError(f"photosIds 每项最多 {MAX_EXTERNAL_PHOTO_ID_LENGTH} 个字符")
    if len(set(normalized)) != len(normalized):
        raise ValueError("同一任务中的 photosIds 不能重复")
    return normalized
