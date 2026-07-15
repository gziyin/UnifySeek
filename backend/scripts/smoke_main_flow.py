import asyncio
import httpx


async def main() -> None:
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=30.0) as client:
        health = await client.get("/api/health")
        print("health", health.status_code, health.json())
        session = await client.post("/api/sessions")
        session_id = session.json()["session_id"]
        print("session", session_id)
        upload = await client.post(
            f"/api/sessions/{session_id}/uploads",
            files={"file": ("notes.txt", b"DeepAgents notes line one\nline two\n", "text/plain")},
        )
        print("upload", upload.status_code, upload.json()["artifact_id"], upload.json()["parse_status"])
        artifact_id = upload.json()["artifact_id"]
        run = await client.post(
            f"/api/sessions/{session_id}/runs",
            json={
                "question": "结合上传笔记分析 DeepAgents 个人项目适用边界并给建议",
                "uploaded_artifact_ids": [artifact_id],
                "max_web_sources": 5,
            },
        )
        print("run", run.status_code, run.json()["run_id"], run.json()["status"])
        run_id = run.json()["run_id"]
        body = {}
        for _ in range(40):
            status = await client.get(f"/api/runs/{run_id}")
            body = status.json()
            if body["status"] == "succeeded":
                print("succeeded", body["report_artifact_id"])
                content = await client.get(f"/api/artifacts/{body['report_artifact_id']}/content")
                print("report_chars", len(content.json()["content"]))
                events = await client.get(f"/api/runs/{run_id}/events?after_seq=0")
                print("events", [item["type"] for item in events.json()["events"]])
                return
            await asyncio.sleep(0.05)
        print("TIMEOUT", body)


if __name__ == "__main__":
    asyncio.run(main())
