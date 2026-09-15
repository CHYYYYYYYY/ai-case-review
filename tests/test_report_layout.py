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
    assert "RepairMove" in html
    assert "箱号识别来源" in html
    assert "photosId:" in html


def test_each_report_item_has_persistent_manual_review_controls() -> None:
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

    assert "AI 审核结果是否正确" in html
    assert "AI 审核正确" in html
    assert "AI 审核错误" in html
    assert "错误原因（选择“AI 审核错误”时必填）" in html
    assert "submitManualReview" in html
    assert "/manual-review`" in html
