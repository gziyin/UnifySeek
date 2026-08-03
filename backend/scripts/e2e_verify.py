"""真实 Agent 端到端验收脚本：创建会话 → 上传文档 → 运行 → 轮询 → 校验报告。"""
from __future__ import annotations

import json
import sys
import time

import httpx

BASE = "http://127.0.0.1:7000"


def main() -> int:
    with httpx.Client(base_url=BASE, timeout=30) as client:
        # 1. 创建会话
        resp = client.post("/api/sessions")
        resp.raise_for_status()
        session_id = resp.json()["session_id"]
        print(f"[1] session={session_id}")

        # 2. 上传文档
        with open(r"D:\code\Projects\DeepSearch_Agent\ai_dev_researcher\backend\demo_note.md", "rb") as fh:
            resp = client.post(
                f"/api/sessions/{session_id}/uploads",
                files={"file": ("demo_note.md", fh, "text/markdown")},
            )
        resp.raise_for_status()
        artifact_id = resp.json()["artifact_id"]
        print(f"[2] uploaded artifact={artifact_id}")

        # 3. 创建 run（真实 Agent）— 聚焦小问题，限制知识库漫游，加快收敛
        payload = {
            "question": "DeepAgents 与 LangGraph 在智能体编排上有什么核心差异？请用 2-3 个要点简要说明。",
            "uploaded_artifact_ids": [artifact_id],
            "max_web_sources": 5,
        }
        resp = client.post(f"/api/sessions/{session_id}/runs", json=payload)
        resp.raise_for_status()
        run = resp.json()
        run_id = run["run_id"]
        print(f"[3] run={run_id} status={run['status']}")

        # 4. 轮询状态与事件
        deadline = time.time() + 180
        last_seq = 0
        event_types: set[str] = set()
        while time.time() < deadline:
            resp = client.get(f"/api/runs/{run_id}")
            resp.raise_for_status()
            status = resp.json()["status"]
            events = client.get(f"/api/runs/{run_id}/events?after_seq={last_seq}").json()
            for ev in events.get("events", []):
                event_types.add(ev["type"])
                if ev["seq"] > last_seq:
                    last_seq = ev["seq"]
            if status in {"succeeded", "failed", "cancelled", "interrupted"}:
                print(f"[4] terminal status={status}")
                break
            time.sleep(3)
        else:
            print("[4] TIMEOUT waiting for run")
            return 1

        # 5. 校验结果
        run = client.get(f"/api/runs/{run_id}").json()
        print(f"[5] run status={run['status']} error={run.get('error_message')}")
        print(f"    events seen: {sorted(event_types)}")
        if run["status"] != "succeeded":
            return 2

        report_id = run.get("report_artifact_id")
        if not report_id:
            print("[6] no report artifact")
            return 3
        report = client.get(f"/api/artifacts/{report_id}/content").text
        print(f"[6] report chars={len(report)}")
        print("---- 报告前 40 行 ----")
        print("\n".join(report.splitlines()[:40]))
        if "[DEGRADED]" in report:
            print("[7] !! DEGRADED report")
            return 4
        print("[7] OK: report is valid (not degraded)")
        return 0


if __name__ == "__main__":
    sys.exit(main())
