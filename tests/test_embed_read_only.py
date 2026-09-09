from __future__ import annotations

from pathlib import Path

from app.api.middleware import APIKeyMiddleware


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_embed_routes_bypass_global_key_for_their_own_scoped_auth() -> None:
    assert "/api/v1/embed-tokens" in APIKeyMiddleware.EXEMPT_PATHS


def test_browser_session_login_bypasses_global_key_for_credential_exchange() -> None:
    assert "/api/v1/session" in APIKeyMiddleware.EXEMPT_PATHS


def test_admin_ui_has_no_delete_or_clear_history_controls() -> None:
    html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")

    assert "clearAllHistory" not in html
    assert "deleteTask" not in html
    assert "清空所有历史" not in html
    assert "method: 'DELETE'" not in html


def test_automatic_history_cleanup_is_disabled() -> None:
    celery_source = (PROJECT_ROOT / "app" / "worker" / "celery_app.py").read_text(encoding="utf-8")
    task_source = (PROJECT_ROOT / "app" / "worker" / "tasks.py").read_text(encoding="utf-8")

    assert "celery_app.conf.beat_schedule = {}" in celery_source
    assert '"disabled": True' in task_source
    assert "session.delete" not in task_source


def test_embed_page_is_read_only_and_uses_fragment_token() -> None:
    html = (PROJECT_ROOT / "static" / "embed.html").read_text(encoding="utf-8")

    assert "location.hash" in html
    assert "Authorization: `Embed ${token}`" in html
    assert "打印报告" in html
    assert "修改" in html
    assert "删除" in html
    assert "取消" in html
    assert "method: 'DELETE'" not in html
    assert "method: 'POST'" not in html


def test_admin_upload_generates_customer_photo_ids() -> None:
    html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")

    assert "fd.append('photosIds'" in html
    assert "j.photo_mappings" in html


def test_admin_ui_uses_short_lived_browser_session_for_api_key() -> None:
    html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")

    assert "API 访问鉴权" in html
    assert "fetch('/api/v1/session'" in html
    assert "X-API-Key" in html
    assert "sessionStorage.setItem" not in html
    assert "localStorage.setItem" not in html
