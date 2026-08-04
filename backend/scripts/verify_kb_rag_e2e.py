"""聚焦知识库 RAG 的真实 Agent 端到端验证脚本（QA 集成验证用）。

目的：验证 document-analyst 子代理实际调用 search_knowledge_base。
通过 constraints 引导模型优先查阅本地知识库源码（deepagents/langchain/langgraph），
并在事件流中核实 agent.started(document-analyst) 与 search_knowledge_base 工具调用。

用法：需后端服务运行于 http://127.0.0.1:7000，且 .env 配置了真实 API key。
"""
from __future__ import annotations

import sys
import time

import httpx

BASE = "http://127.0.0.1:7000"


def main() -> int:
    with httpx.Client(base_url=BASE, timeout=30) as client:
        resp = client.post("/api/sessions")
        resp.raise_for_status()
        session_id = resp.json()["session_id"]
        print(f"[1] session={session_id}")

        payload = {
            "question": (
                "在本地知识库 deepagents 源码中，create_deep_agent 函数定义在哪个文件，"
                "它接受哪些核心参数（如 model/system_prompt/tools/subagents）？"
                "请用 2-3 个要点简要说明。"
            ),
            "constraints": [
                "必须使用本地知识库检索（search_knowledge_base 或 list/read_knowledge_base_file）回答，不要仅依赖网络搜索",
            ],
            "max_web_sources": 3,
        }
        resp = client.post(f"/api/sessions/{session_id}/runs", json=payload)
        resp.raise_for_status()
        run = resp.json()
        run_id = run["run_id"]
        print(f"[2] run={run_id} status={run['status']}")

        deadline = time.time() + 240
        last_seq = 0
        tool_events: list[dict] = []
        agent_events: list[str] = []
        while time.time() < deadline:
            status = client.get(f"/api/runs/{run_id}").json()["status"]
            events = client.get(f"/api/runs/{run_id}/events?after_seq={last_seq}").json().get("events", [])
            for ev in events:
                t = ev["type"]
                if t == "agent.started":
                    agent_events.append(str(ev.get("payload", {}).get("agent_name")))
                if t in ("tool.started", "tool.completed"):
                    tool_events.append(
                        {
                            "seq": ev.get("seq"),
                            "type": t,
                            "tool": (ev.get("payload") or {}).get("tool_name"),
                        }
                    )
                if ev["seq"] > last_seq:
                    last_seq = ev["seq"]
            if status in {"succeeded", "failed", "cancelled", "interrupted"}:
                print(f"[3] terminal status={status}")
                break
            time.sleep(3)
        else:
            print("[3] TIMEOUT waiting for run")
            return 1

        run = client.get(f"/api/runs/{run_id}").json()
        print(f"[4] run status={run['status']} error={run.get('error_message')}")
        print(f"    agents started: {agent_events}")
        kb_tools = [e for e in tool_events if "knowledge" in str(e.get("tool"))]
        print(f"    KB tool events: {len(kb_tools)}")
        for e in tool_events:
            print(f"    seq={e['seq']} {e['type']} tool={e['tool']}")

        if run["status"] != "succeeded":
            print("[5] run did not succeed")
            return 2

        if not kb_tools:
            print("[6] !! document-analyst did not call search_knowledge_base (model did not delegate)")
            return 3

        report_id = run.get("report_artifact_id")
        report = client.get(f"/api/artifacts/{report_id}/content").text if report_id else ""
        degraded = "[DEGRADED]" in report
        print(f"[7] report chars={len(report)} degraded={degraded}")
        print("---- 报告前 30 行 ----")
        print("\n".join(report.splitlines()[:30]))
        return 0 if not degraded else 4


if __name__ == "__main__":
    sys.exit(main())
