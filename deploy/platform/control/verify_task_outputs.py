"""Real authorized demo tasks, retained outputs, scope denials and digest verification.

The model is real. Inputs are synthetic demonstration material. No task is deleted.
Run after API/worker publication; re-running checks the same retained task IDs.
"""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
import urllib.error
import urllib.request
from uuid import uuid4

from demo_session import DemoSession
from provision_data import ROOT, DATABASE, command, private_file

REPORT = ROOT / "task-output-verification.json"


def raw(session, route, expected=200):
    request = urllib.request.Request(session.api + route, headers=session.auth)
    try:
        response = session.opener.open(request, timeout=60)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        assert response.status == expected, (route, response.status)
        return response.read(34 * 1024 * 1024), dict(response.headers)


def complete(session, identifier):
    for _ in range(180):
        state = session.call("/api/v1/runs/" + identifier)
        if state["state"] in ("succeeded", "failed", "cancelled", "timed_out"):
            return state
        time.sleep(2)
    raise RuntimeError("Output demonstration task did not finish in time")


def main():
    session = DemoSession()
    evidence = (
        json.loads(REPORT.read_text())
        if REPORT.exists()
        else {"runs": {}, "outputs": {}}
    )
    deployments = session.call(session.management + "/agent-deployments")
    # Select existing demo deployments by immutable capability, never by model-controlled names.
    candidates = []
    for item in deployments:
        details = session.call("/api/v1/agent-deployments/" + item["deployment_id"])
        candidates.append(details)
    # The SQL only reads capability bindings to select a known demonstration target.
    bound = command(
        "docker",
        "exec",
        "-i",
        "open-agent-control-mysql",
        "sh",
        "-c",
        'MYSQL_PWD="$(cat /run/secrets/mysql-root)" mysql -N -uroot ' + DATABASE,
        input="SELECT DISTINCT deployment_id FROM oa_agent_deployment_skill WHERE tenant_id='space-1' AND environment='test';",
    ).stdout.split()
    choices = {
        "sandbox": next(
            d
            for d in candidates
            if d["deployment_id"] in bound and d["state"] == "test"
        ),
        "state": next(
            d
            for d in candidates
            if d["deployment_id"] not in bound and d["state"] == "test"
        ),
    }
    prompts = {
        "state": "请调用 write_file 工具创建 /workspace/outputs/项目周报.csv。内容为：项目,状态,负责人\nAgent平台,测试中,演示团队\n资料归档,已验证,演示团队。再创建 /workspace/outputs/交付说明.md 说明这两个文件用于演示任务产物持久保存。必须实际创建文件，最终回答简短。",
        "sandbox": "这是资料交付演示，请使用 execute 工具运行 Python 标准库，实际创建 /workspace/outputs/交付摘要.txt、/workspace/outputs/交付清单.csv 和 /workspace/outputs/交付数据.bin。前两个文件用中文写明 Agent平台演示、负责人演示团队、状态待验收；二进制文件内容为 bytes(range(256))*8192，精确 2097152 字节。仅用标准库，不联网安装。所有文件必须保存到该目录。最后简短确认。",
    }
    for kind in ("state", "sandbox"):
        if kind not in evidence["runs"]:
            accepted = session.call(
                "/api/v1/agent-deployments/" + choices[kind]["deployment_id"] + "/runs",
                {
                    "input": {"message": prompts[kind], "history": []},
                    "timeoutSeconds": 300,
                },
                headers={"Idempotency-Key": "output-demo-" + kind + "-" + str(uuid4())},
            )
            evidence["runs"][kind] = accepted["run_id"]
            private_file(REPORT, json.dumps(evidence, indent=2) + "\n", replace=True)
        identifier = evidence["runs"][kind]
        state = complete(session, identifier)
        assert state["state"] == "succeeded", (
            kind,
            state["state"],
            state.get("error_code"),
        )
        rows = session.call("/api/v1/runs/" + identifier + "/outputs")
        assert len(rows) >= 2, (kind, "missing_outputs")
        evidence["outputs"][kind] = []
        for row in rows:
            assert row["state"] == "available"
            route = "/api/v1/runs/" + identifier + "/outputs/" + row["output_id"]
            body, headers = raw(session, route + "/download")
            assert (
                hashlib.sha256(body).hexdigest() == row["sha256"]
                and len(body) == row["size_bytes"]
            )
            assert headers.get("Content-Type") == "application/octet-stream"
            assert headers.get("Content-Disposition", "").startswith("attachment;")
            assert headers.get("X-Content-Type-Options") == "nosniff"
            assert headers.get("Cache-Control") == "no-store"
            if row["file_name"].endswith((".txt", ".csv", ".md")):
                assert session.call(route + "/preview")["text"] == body.decode()
            evidence["outputs"][kind].append(
                {
                    key: row[key]
                    for key in (
                        "output_id",
                        "file_name",
                        "size_bytes",
                        "sha256",
                        "step_id",
                    )
                }
            )
        if kind == "sandbox":
            assert any(row["size_bytes"] == 2097152 for row in rows)
        print(
            json.dumps(
                {
                    "kind": kind,
                    "run_id": identifier,
                    "files": len(rows),
                    "download_digests_verified": True,
                }
            ),
            flush=True,
        )
    other = DemoSession(
        "member-demo",
        json.loads((ROOT / "member-secrets.json").read_text())["password"],
        require_tenant=False,
    )
    for kind, identifier in evidence["runs"].items():
        raw(other, "/api/v1/runs/" + identifier + "/outputs", 404)
        for item in evidence["outputs"][kind]:
            raw(
                other,
                "/api/v1/runs/"
                + identifier
                + "/outputs/"
                + item["output_id"]
                + "/download",
                404,
            )
    evidence["verified_at"] = datetime.now(timezone.utc).isoformat()
    evidence["cross_tenant_denied"] = True
    private_file(
        REPORT, json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", replace=True
    )
    print("Retained task output verification passed", flush=True)


if __name__ == "__main__":
    main()
