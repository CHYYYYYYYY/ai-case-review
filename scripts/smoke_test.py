"""本地联调冒烟测试脚本.

用法:
    python scripts/smoke_test.py

需要先启动 API 服务(uvicorn)与 Worker(celery).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx

API_BASE = "http://localhost:8000"


def main() -> int:
    if len(sys.argv) < 3:
        print("用法: python smoke_test.py <manifest_image> <photo1> [photo2] ...")
        print("示例: python scripts/smoke_test.py tests/fixtures/manifest.jpg tests/fixtures/p1.jpg")
        return 1

    manifest = Path(sys.argv[1])
    photos = [Path(p) for p in sys.argv[2:]]
    if not manifest.exists():
        print(f"清单图不存在: {manifest}")
        return 1
    for p in photos:
        if not p.exists():
            print(f"照片不存在: {p}")
            return 1

    # 健康检查
    with httpx.Client(timeout=5) as c:
        r = c.get(f"{API_BASE}/health")
        if r.status_code != 200:
            print(f"API 未启动, /health 返回 {r.status_code}")
            return 1
        print("[OK] /health")

    # 提交任务
    with open(manifest, "rb") as mf:
        files = [("manifest_image", (manifest.name, mf, "image/jpeg"))]
        for p in photos:
            files.append(("photos", (p.name, open(p, "rb"), "image/jpeg")))
        with httpx.Client(timeout=30) as c:
            r = c.post(f"{API_BASE}/api/v1/audits", files=files, data={})
        # 关闭文件句柄
        for _, (_, fh, _) in files[1:]:
            fh.close()

    if r.status_code != 202:
        print(f"提交失败: {r.status_code} {r.text}")
        return 1

    task = r.json()
    task_id = task["task_id"]
    print(f"[OK] 任务已提交: task_id={task_id}")

    # 轮询状态
    print("等待任务完成...")
    for i in range(120):  # 最多等 10 分钟
        time.sleep(5)
        with httpx.Client(timeout=5) as c:
            r = c.get(f"{API_BASE}/api/v1/audits/{task_id}")
        data = r.json()
        status = data["status"]
        stage = data["current_stage"]
        detail = data["progress"]["detail"]
        print(f"  [{i*5:>3}s] {status} | {stage} | {detail}")
        if status in ("succeeded", "failed", "cancelled"):
            break

    # 拉报告
    with httpx.Client(timeout=5) as c:
        r = c.get(f"{API_BASE}/api/v1/audits/{task_id}/report")

    if r.status_code == 200:
        import json
        report = r.json()
        print("\n========== 稽核报告 ==========")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    else:
        print(f"获取报告失败: {r.status_code} {r.text}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
