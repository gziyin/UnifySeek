"""手动核对清单可脚本化项验证（QA 集成验证用，v2 修正）。

覆盖：
- 超长问题（>4000 字符）可通过 POST /api/runs 校验创建（创建后立即取消，避免真实消耗）。
- GET /api/sessions 列表带 display_name 字段；POST /api/sessions create 响应带 display_name 字段。
- 新 session 首次 run 后 sessions/ 出现 slug-8位短uuid 目录（display_name 由首次 run 派生）。

用法：需后端服务运行于 http://127.0.0.1:7000。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:7000"
SESSIONS_ROOT = Path(r"D:\code\Projects\DeepSearch_Agent\ai_dev_researcher\backend\sessions")


def main() -> int:
    fails: list[str] = []
    with httpx.Client(base_url=BASE, timeout=30) as client:
        # ---- 1. POST /api/sessions：响应带 display_name 字段（首次 run 前为 None，属预期）----
        resp = client.post("/api/sessions")
        resp.raise_for_status()
        body = resp.json()
        sid = body["session_id"]
        print(f"[1] POST /api/sessions status={resp.status_code} has display_name field={'display_name' in body} "
              f"display_name={body.get('display_name')!r}")
        if "display_name" not in body:
            fails.append("POST /api/sessions 响应缺 display_name 字段")

        # ---- 2. GET /api/sessions：列表项带 display_name ----
        resp = client.get("/api/sessions")
        resp.raise_for_status()
        sessions = resp.json()
        items = sessions if isinstance(sessions, list) else sessions.get("sessions", [])
        print(f"[2] GET /api/sessions count={len(items)}")
        if not items:
            fails.append("GET /api/sessions 为空")
        elif "display_name" not in set(items[0].keys()):
            fails.append("GET /api/sessions 列表项缺 display_name 字段")
        else:
            named = [it for it in items if it.get("display_name")]
            print(f"    items with display_name: {len(named)}/{len(items)}")

        # ---- 3. 超长问题（>4000 字符）POST /api/runs 通过校验 ----
        long_q = "超长问题验证：" + "研究内容" * 1000
        print(f"[3] long question chars={len(long_q)}")
        if len(long_q) <= 4000:
            fails.append(f"测试问题长度不足 4000: {len(long_q)}")
        resp = client.post(
            f"/api/sessions/{sid}/runs",
            json={"question": long_q, "max_web_sources": 3},
        )
        if resp.status_code >= 400:
            fails.append(f"超长问题被拒绝: HTTP {resp.status_code} {resp.text[:200]}")
        else:
            long_run_id = resp.json()["run_id"]
            print(f"    accepted run={long_run_id} status={resp.json()['status']}")
            c = client.post(f"/api/runs/{long_run_id}/cancel")
            print(f"    cancel status={c.status_code}")

        # ---- 4. slug 目录验证：新 session 首次 run 后出现 slug-8位短uuid 目录 ----
        resp = client.post("/api/sessions")
        resp.raise_for_status()
        sid2 = resp.json()["session_id"]
        question = "DeepAgents 边界分析如何实现"
        resp = client.post(
            f"/api/sessions/{sid2}/runs",
            json={"question": question, "max_web_sources": 3},
        )
        if resp.status_code >= 400:
            fails.append(f"首次 run 创建失败: HTTP {resp.status_code} {resp.text[:200]}")
        else:
            run_id2 = resp.json()["run_id"]
            print(f"[4] first-run session={sid2} run={run_id2}")
            client.post(f"/api/runs/{run_id2}/cancel")
            time.sleep(0.5)
            # slug 目录 = make_slug(question)-8位短uuid（首 run 命名在 create_run 同步完成）
            slug_dir = SESSIONS_ROOT / f"DeepAgents-边界分析如何实现-{sid2[:8]}"
            print(f"    slug dir exists={slug_dir.exists()} name={slug_dir.name}")
            if not slug_dir.exists():
                fails.append(f"首次 run 未创建 slug 目录: {slug_dir.name}")

        # ---- 5. 目录盘点：UUID 目录（存量/上传路径）与 slug 目录并存 ----
        uuid_dirs = [p.name for p in SESSIONS_ROOT.iterdir() if p.is_dir() and len(p.name) == 36]
        slug_dirs = [p.name for p in SESSIONS_ROOT.iterdir() if p.is_dir() and "-" in p.name and len(p.name) > 36]
        print(f"[5] uuid dirs={len(uuid_dirs)} slug dirs={len(slug_dirs)}")
        print(f"    slug samples: {slug_dirs[:4]}")

    print("\n==== RESULT ====")
    if fails:
        print("FAIL:")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
