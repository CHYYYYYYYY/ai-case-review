"""PAA 部件标记规则(纯代码).

P3-B 对 component 主码为 PAA 的维修项, 根据 Location 面代码推断面板方位.
"""
from __future__ import annotations

_FACE_NAMES = {
    "L": "左侧面",
    "R": "右侧面",
    "T": "顶板",
    "B": "底板",
    "D": "门端",
    "F": "前端",
}


def mark_paa(component: str | None, location_code: str | None) -> tuple[bool, str | None]:
    """返回 (is_paa, panel_face).

    is_paa: 该维修项是否为波纹面板(PAA)类
    panel_face: 从 Location 首字母推断的面方位中文名, 无法推断则为 None
    """
    if not component:
        return False, None
    primary = component.split("/", 1)[0].strip().upper()
    if primary != "PAA":
        return False, None
    face_code = (location_code or "").strip().upper()[:1]
    return True, _FACE_NAMES.get(face_code)
