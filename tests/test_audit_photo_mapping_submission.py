from __future__ import annotations

from io import BytesIO

import pytest
from starlette.datastructures import Headers, UploadFile

from app.api import audits
from app.db.models import TaskPhoto


class _FakeSession:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.committed = False

    def add(self, value: object) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self.committed = True


class _FakeObjectStore:
    def __init__(self) -> None:
        self.counter = 0

    def upload_image(self, data: bytes, *, ext: str) -> str:
        assert data
        self.counter += 1
        return f"objects/upload-{self.counter}.{ext}"


def _image(filename: str, content: bytes) -> UploadFile:
    return UploadFile(
        file=BytesIO(content),
        filename=filename,
        headers=Headers({"content-type": "image/jpeg"}),
    )


@pytest.mark.asyncio
async def test_submit_returns_ordered_customer_to_internal_photo_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeSession()
    queued: list[str] = []
    monkeypatch.setattr(audits, "get_object_store", lambda: _FakeObjectStore())
    monkeypatch.setattr(audits.run_audit_pipeline, "delay", queued.append)

    response = await audits.create_audit(
        manifest_image=_image("manifest.jpg", b"manifest"),
        photos=[_image("photo_A.jpg", b"A"), _image("photo_B.jpg", b"B")],
        photos_ids=["COMPANY_PHOTO_001", "COMPANY_PHOTO_002"],
        callback_url=None,
        metadata=None,
        session=session,  # type: ignore[arg-type]
    )

    assert session.committed is True
    assert queued == [response["task_id"]]
    assert response["photo_id_list"] == [
        response["photo_mappings"][0]["photo_id"],
        response["photo_mappings"][1]["photo_id"],
    ]
    assert [mapping["photosId"] for mapping in response["photo_mappings"]] == [
        "COMPANY_PHOTO_001",
        "COMPANY_PHOTO_002",
    ]
    assert [mapping["seq"] for mapping in response["photo_mappings"]] == [1, 2]
    assert [mapping["filename"] for mapping in response["photo_mappings"]] == [
        "photo_A.jpg",
        "photo_B.jpg",
    ]

    saved_photos = [value for value in session.added if isinstance(value, TaskPhoto)]
    assert [photo.external_photo_id for photo in saved_photos] == [
        "COMPANY_PHOTO_001",
        "COMPANY_PHOTO_002",
    ]
    assert [photo.photo_id for photo in saved_photos] == response["photo_id_list"]
