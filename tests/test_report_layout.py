from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_main_and_history_reports_show_details_without_expand_click() -> None:
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

    assert html.count('<div class="verification-list">${rows}</div>') == 2
    assert html.count('<div class="verification-details">') == 2
    assert html.count('<div class="verification-result-wrap">') == 2
    assert "toggleItemExpand" not in html
    assert "点击展开详情" not in html
    assert "已通过" in html
    assert "部分通过" in html
    assert "未通过" in html
