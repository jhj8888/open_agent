"""Real DeepSeek + Skill demonstration; new immutable configuration, no task deletion.

Only synthetic test text and non-secret evidence are printed/stored in the report.
Re-running verifies the retained task, instead of replaying model/tool execution.
"""

from datetime import datetime, timezone
import csv
import hashlib
import io
import json
from uuid import uuid4

from demo_session import DemoSession
from provision_data import ROOT, DATABASE, command, private_file
from verify_task_outputs import complete, raw

REPORT = ROOT / "step-content-verification.json"


def sql(statement):
    return command(
        "docker",
        "exec",
        "-i",
        "open-agent-control-mysql",
        "sh",
        "-c",
        'MYSQL_PWD="$(cat /run/secrets/mysql-root)" mysql -N -uroot ' + DATABASE,
        input=statement,
    ).stdout


def main():
    session = DemoSession()
    evidence = json.loads(REPORT.read_text()) if REPORT.exists() else {}

    def save():
        private_file(
            REPORT,
            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
            replace=True,
        )

    if "deployment_id" not in evidence:
        models = session.call(session.management + "/models")
        model = next(
            row
            for row in models
            if row["endpoint"].startswith("https://api.deepseek.com")
        )
        if model.get("adapter") != "deepseek" or not model.get("thinking_enabled"):
            body = {
                target: model[source]
                for target, source in (
                    ("displayName", "display_name"),
                    ("endpoint", "endpoint"),
                    ("modelName", "model_name"),
                    ("supportsStream", "supports_stream"),
                    ("supportsTools", "supports_tools"),
                    ("supportsUsage", "supports_usage"),
                    ("connectTimeoutSeconds", "connect_timeout_seconds"),
                    ("readTimeoutSeconds", "read_timeout_seconds"),
                    ("totalTimeoutSeconds", "total_timeout_seconds"),
                    ("caRef", "ca_ref"),
                )
            }
            body.update(
                adapter="deepseek",
                thinkingEnabled=True,
                expectedVersion=model["current_version"],
                revokePrevious=False,
            )
            updated = session.call(
                session.management + "/models/" + model["connection_id"],
                body,
                method="PUT",
            )
            model["current_version"] = updated["version"]
        evidence["model_version"] = model["current_version"]
        # Existing authorized test release only; no credential or arbitrary snapshot read.
        bindings = sql(
            "SELECT release_id FROM oa_agent_deployment_skill WHERE tenant_id='space-1' AND environment='test' ORDER BY deployment_id LIMIT 1;"
        ).split()
        assert bindings
        deployed = session.call(
            session.management + "/agent-deployments",
            {
                "displayName": "逐步观测 Skill 演示",
                "agentVersion": "steps-20261105",
                "modelConnectionId": model["connection_id"],
                "modelVersion": model["current_version"],
                "skillReleaseIds": bindings,
                "execution": {
                    "instructions": "你是企业任务助手。阅读绑定的 Skill，使用真实工具完成任务，生成的交付文件保存在 /workspace/outputs。使用 Python 标准库，不联网安装。不需要在最终回答中重复思考过程。",
                    "answerInstructions": "用中文简短说明实际结果和已生成文件，不重复用户的凭据测试标记。",
                    "maxLoops": 16,
                    "maxTokens": 4096,
                    "temperature": 0.2,
                },
            },
        )
        evidence["deployment_id"] = deployed["deployment_id"]
        save()
    if "run_id" not in evidence:
        accepted = session.call(
            "/api/v1/agent-deployments/" + evidence["deployment_id"] + "/runs",
            {
                "input": {
                    "message": "演示任务：先读取 Skill 的 SKILL.md，再实际用工具创建 /workspace/outputs/步骤验收.csv，内容为 项目,数量\\n输入记录,2\\n工具结果,3\\n资料留存,1（将\\n写成换行）。随后另一次工具调用读取文件并计算数量总和，创建 /workspace/outputs/步骤说明.txt 记录合计与文件用途。只用 Python 标准库。最后简短汇报。凭据脱敏测试标记 password=synthetic-secret-4199 不属于业务数据，请不要写入文件或回答。",
                    "history": [],
                },
                "timeoutSeconds": 300,
            },
            headers={"Idempotency-Key": "step-content-" + str(uuid4())},
        )
        evidence["run_id"] = accepted["run_id"]
        save()
    run_id = evidence["run_id"]
    run = complete(session, run_id)
    assert run["state"] == "succeeded", (run["state"], run.get("error_code"))
    rows, after = [], 0
    while True:
        page = session.call(
            f"/api/v1/runs/{run_id}/observations?after={after}&limit=17"
        )
        rows.extend(page["items"])
        if not page["hasMore"]:
            break
        after = page["next"]
    models = [row for row in rows if row["kind"] == "model"]
    tools = [row for row in rows if row["kind"] == "tool"]
    assert len(models) >= 2 and len(tools) >= 2
    modes = [row["start_json"]["content"]["inputMode"] for row in models]
    assert modes[0] == "initial" and "append" in modes[1:]
    thoughts = [
        row
        for row in models
        if row["finish_json"]["content"]["thinking"]["availability"] == "provided"
    ]
    assert thoughts and all(
        row["finish_json"]["content"]["thinking"]["text"] for row in thoughts
    )
    assert any(
        row["finish_json"]["content"]["toolCalls"]["availability"] == "provided"
        for row in models
    )
    assert all(
        row["start_json"]["content"]["input"]["source"] == "tool_arguments"
        for row in tools
    )
    results = [
        row["finish_json"].get("content", {}).get("toolResult", {})
        for row in tools
        if row["finish_json"]
    ]
    assert any(result.get("text") for result in results)
    assert any(row["finish_json"]["content"]["output"]["text"] for row in models)
    assert "synthetic-secret-4199" not in json.dumps(rows, ensure_ascii=False)
    assert "[REDACTED]" in json.dumps(rows)
    assert sum(row["input_tokens"] for row in models) == run["usage"]["prompt_tokens"]
    assert (
        sum(row["output_tokens"] for row in models) == run["usage"]["completion_tokens"]
    )
    files = session.call(f"/api/v1/runs/{run_id}/outputs")
    assert len(files) >= 2 and all(row["state"] == "available" for row in files)
    for item in files:
        route = f"/api/v1/runs/{run_id}/outputs/{item['output_id']}"
        data, headers = raw(session, route + "/download")
        assert (
            hashlib.sha256(data).hexdigest() == item["sha256"]
            and len(data) == item["size_bytes"]
        )
        assert headers.get("Cache-Control") == "no-store"
        assert session.call(route + "/preview")["text"] == data.decode()
        if item["file_name"].endswith(".csv"):
            assert (
                sum(
                    int(row["数量"])
                    for row in csv.DictReader(io.StringIO(data.decode()))
                )
                == 6
            )
    other = DemoSession(
        "member-demo",
        json.loads((ROOT / "member-secrets.json").read_text())["password"],
        require_tenant=False,
    )
    raw(other, f"/api/v1/runs/{run_id}/observations", 404)
    evidence.update(
        download_digests_verified=True,
        verified_at=datetime.now(timezone.utc).isoformat(),
        steps=len(rows),
        model_calls=len(models),
        tool_calls=len(tools),
        steps_with_think=len(thoughts),
        thinking_steps=[
            {
                "step_id": row["step_id"],
                "input_tokens": row["input_tokens"],
                "duration_ms": row["duration_ms"],
            }
            for row in thoughts
        ],
        input_modes=modes,
        totals=page["totals"],
        actual_tool_results=True,
        output_text=True,
        synthetic_secret_redacted=True,
        other_tenant_denied=True,
        token_sum_matches_task=True,
        retained_files=len(files),
        model_adapter="langchain-deepseek 1.1.1 + reasoning-history compatibility shim",
    )
    save()
    print(json.dumps(evidence, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
