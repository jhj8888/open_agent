"""Real Skill trace + retained file + natural-period authorization verification.

Reuses a previously submitted verification run; never deletes tasks or artifacts.
"""

from datetime import datetime, timezone
import json
from uuid import uuid4

from demo_session import DemoSession
from provision_data import ROOT, DATABASE, command, private_file
from verify_task_outputs import complete, raw

REPORT = ROOT / "agent-observability-verification.json"


def main(new_run=False):
    session = DemoSession()
    evidence = json.loads(REPORT.read_text()) if REPORT.exists() else {}
    if new_run and "run_id" in evidence:
        previous = session.call("/api/v1/runs/" + evidence["run_id"])
        assert previous["state"] in {"succeeded", "failed", "cancelled", "timed_out"}
        evidence = {
            "previous_runs": evidence.get("previous_runs", []) + [evidence["run_id"]]
        }
    if "run_id" not in evidence:
        bound = command(
            "docker",
            "exec",
            "-i",
            "open-agent-control-mysql",
            "sh",
            "-c",
            'MYSQL_PWD="$(cat /run/secrets/mysql-root)" mysql -N -uroot ' + DATABASE,
            input="SELECT DISTINCT deployment_id FROM oa_agent_deployment_skill WHERE environment='test';",
        ).stdout.split()
        deployments = session.call(session.management + "/agent-deployments")
        target = next(
            row
            for row in deployments
            if row["deployment_id"] in bound and row["state"] == "test"
        )
        accepted = session.call(
            "/api/v1/agent-deployments/" + target["deployment_id"] + "/runs",
            {
                "input": {
                    "message": "执行一次 Agent 可观测演示。先阅读当前 Skill 的 SKILL.md，然后实际使用工具创建 /workspace/outputs/观测验收.csv，内容为 项目,完成数\n模型逐步记录,2\n工具调用追踪,3\n任务文件归档,1。接着再次调用工具读取这个 CSV，计算完成数合计，创建 /workspace/outputs/观测说明.txt，写明总数和各项结果。只使用 Python 标准库，不联网安装。最终简短报告实际生成的文件和合计。",
                    "history": [],
                },
                "timeoutSeconds": 300,
            },
            headers={"Idempotency-Key": "observation-demo-" + str(uuid4())},
        )
        evidence["run_id"] = accepted["run_id"]
        private_file(REPORT, json.dumps(evidence, indent=2) + "\n", replace=True)
    run_id = evidence["run_id"]
    state = complete(session, run_id)
    assert state["state"] == "succeeded", (state["state"], state.get("error_code"))
    rows = []
    after = 0
    while True:
        page = session.call(
            f"/api/v1/runs/{run_id}/observations?after={after}&limit=17"
        )
        rows.extend(page["items"])
        after = page["next"]
        if not page["hasMore"]:
            break
    assert len({row["id"] for row in rows}) == len(rows) > 17
    assert {"agent", "chain", "skill", "model", "tool", "artifact"}.issubset(
        {row["kind"] for row in rows}
    )
    assert all(row["status"] in {"succeeded", "failed"} for row in rows)
    assert next(row for row in rows if row["kind"] == "agent")["status"] == "succeeded"
    ids = {row["step_id"] for row in rows}
    assert all(
        row["parent_step_id"] is None or row["parent_step_id"] in ids for row in rows
    )
    models = [row for row in rows if row["kind"] == "model"]
    assert len(models) >= 2 and all(row["usage_source"] == "provider" for row in models)
    assert sum(row["input_tokens"] for row in models) == state["usage"]["prompt_tokens"]
    assert (
        sum(row["output_tokens"] for row in models)
        == state["usage"]["completion_tokens"]
    )
    assert all(row["input_tokens"] is None for row in rows if row["kind"] != "model")
    for row in rows:
        assert row["duration_ms"] is not None and row["duration_ms"] >= 0
        for field in ("start_json", "finish_json"):
            assert set(row[field]["summary"]).issubset(
                {
                    "input",
                    "output",
                    "errorType",
                    "toolCallId",
                    "releaseId",
                    "manifestSha256",
                    "fileCount",
                }
            )
    files = session.call(f"/api/v1/runs/{run_id}/outputs")
    assert len(files) >= 2 and all(row["state"] == "available" for row in files)
    tool_calls = {
        "tool:" + row["start_json"]["summary"]["toolCallId"]
        for row in rows
        if row["kind"] == "tool" and "toolCallId" in row["start_json"]["summary"]
    }
    assert all(
        row["step_id"] in tool_calls or row["step_id"] == "final" for row in files
    )
    reports = {}
    for period in ("this_week", "last_week", "this_month", "last_month"):
        report = session.call(
            session.management + "/observation-statistics?period=" + period
        )
        assert report["timezone"] == "Asia/Shanghai"
        assert sum(int(day["inputTokens"]) for day in report["daily"]) == int(
            report["usage"]["inputTokens"]
        )
        assert sum(int(day["tasks"]) for day in report["daily"]) == int(
            report["tasks"]["tasks"]
        )
        reports[period] = {
            key: report[key] for key in ("start", "end", "asOf", "tasks", "usage")
        }
    other = DemoSession(
        "member-demo",
        json.loads((ROOT / "member-secrets.json").read_text())["password"],
        require_tenant=False,
    )
    for route in (
        f"/api/v1/runs/{run_id}/observations",
        session.management + "/observation-statistics",
    ):
        raw(other, route, 404)
    evidence.update(
        {
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "steps": len(rows),
            "totals": page["totals"],
            "kinds": sorted({row["kind"] for row in rows}),
            "all_steps_finished": True,
            "failed_steps": sum(row["status"] == "failed" for row in rows),
            "parent_links_verified": True,
            "token_sum_matches_task": True,
            "file_tool_links_verified": True,
            "output_files": len(files),
            "two_tenant_denials": 2,
            "periods": reports,
            "external_observation_backend": "not_yet_connected",
        }
    )
    private_file(
        REPORT, json.dumps(evidence, indent=2, ensure_ascii=False) + "\n", replace=True
    )
    print(
        json.dumps(
            {
                key: evidence[key]
                for key in (
                    "run_id",
                    "steps",
                    "totals",
                    "kinds",
                    "output_files",
                    "two_tenant_denials",
                )
            }
        )
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--new-run",
        action="store_true",
        help="Submit a new synthetic verification task; retain previous task IDs",
    )
    main(parser.parse_args().new_run)
