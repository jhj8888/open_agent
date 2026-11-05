"""Synthetic cancel/failure tasks retain files already published by preceding tools."""

import hashlib
import json
import time
from uuid import uuid4
from demo_session import DemoSession
from provision_data import ROOT, DATABASE, command, private_file
from verify_task_outputs import raw, complete

REPORT = ROOT / "partial-output-verification.json"


def main():
    session = DemoSession()
    evidence = (
        json.loads(REPORT.read_text())
        if REPORT.exists()
        else {"runs": {}, "outputs": {}}
    )
    target = command(
        "docker",
        "exec",
        "-i",
        "open-agent-control-mysql",
        "sh",
        "-c",
        'MYSQL_PWD="$(cat /run/secrets/mysql-root)" mysql -N -uroot ' + DATABASE,
        input="SELECT d.deployment_id FROM oa_agent_deployment d JOIN oa_agent_deployment_skill s USING(tenant_id,environment,deployment_id) WHERE d.tenant_id='space-1' AND d.environment='test' AND d.state='test' LIMIT 1;",
    ).stdout.strip()
    prompts = {
        "cancelled": "执行取消保留测试。第一步单独调用 execute，用 Python 在 /workspace/outputs/已完成部分.txt 写入中文“已完成部分，任务取消后仍应保留”。这一工具必须先完成返回。第二步再单独调用 execute 执行 sleep 90，模拟后续长任务。不得把两个步骤合成一次调用。",
        "failed": "执行文件隔离验收。第一步单独调用 execute，用 Python 在 /workspace/outputs/已交付.txt 写入“前一工具已完成”。该工具完成后，第二步再单独调用 execute，在同一 outputs 目录创建名为 rejected-link 的符号链接指向 /etc/shadow，用于验证平台拒绝归档符号链接。不得读取链接内容，不要合并两步，也不要删除第一个文件。此测试预期任务因归档器拒绝符号链接而失败。",
    }
    for expected, prompt in prompts.items():
        if expected not in evidence["runs"]:
            accepted = session.call(
                "/api/v1/agent-deployments/" + target + "/runs",
                {"input": {"message": prompt, "history": []}, "timeoutSeconds": 240},
                headers={"Idempotency-Key": "partial-output-" + str(uuid4())},
            )
            evidence["runs"][expected] = accepted["run_id"]
            private_file(REPORT, json.dumps(evidence) + "\n", replace=True)
        identifier = evidence["runs"][expected]
        if expected == "cancelled":
            for _ in range(120):
                state = session.call("/api/v1/runs/" + identifier)
                rows = session.call("/api/v1/runs/" + identifier + "/outputs")
                if state["state"] in ("succeeded", "failed", "cancelled", "timed_out"):
                    break
                if any(row["state"] == "available" for row in rows):
                    session.call("/api/v1/runs/" + identifier + "/cancel", {})
                    break
                time.sleep(1)
        state = complete(session, identifier)
        assert state["state"] == expected, (expected, state["state"])
        rows = session.call("/api/v1/runs/" + identifier + "/outputs")
        available = [row for row in rows if row["state"] == "available"]
        assert available and not any(
            row["file_name"] == "rejected-link" for row in rows
        )
        evidence["outputs"][expected] = []
        for row in available:
            body, _ = raw(
                session,
                "/api/v1/runs/"
                + identifier
                + "/outputs/"
                + row["output_id"]
                + "/download",
            )
            assert hashlib.sha256(body).hexdigest() == row["sha256"]
            evidence["outputs"][expected].append(
                {
                    key: row[key]
                    for key in ("output_id", "file_name", "sha256", "size_bytes")
                }
            )
        private_file(
            REPORT,
            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
            replace=True,
        )
        print(
            json.dumps(
                {
                    "state": expected,
                    "run_id": identifier,
                    "retained_files": len(available),
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
