"""Real MLflow, model/Skill outage recovery and tenant access checks; no task deletion/re-execution."""

import json
import os
from pathlib import Path
import sys
import time

ROOT = Path("/opt/open-agent/observability")
sys.path.insert(0, "/opt/open-agent/verification/control-package")
from demo_session import DemoSession
from provision_data import command, private_file
from verify_step_content import sql
from verify_task_outputs import complete, raw

REPORT = ROOT / "delivery-verification.json"


def main():
    os.environ.update(
        MLFLOW_DISABLE_TELEMETRY="true",
        NO_PROXY="*",
        MLFLOW_HTTP_REQUEST_MAX_RETRIES="0",
    )
    deployed = json.loads((ROOT / "exporter-deployment.json").read_text())
    sys.path.insert(0, str(ROOT / "sources" / deployed["source_sha256"]))
    from agent.observability.exporter import project, official_exporter, DurableExporter
    from mlflow import MlflowClient
    from mlflow.server import get_app_client
    from mlflow.exceptions import MlflowException

    credentials = json.loads((ROOT / "backend-secrets.json").read_text())
    config = json.loads((ROOT / "exporter-config.json").read_text())
    os.environ.update(
        MLFLOW_TRACKING_USERNAME=credentials["admin_user"],
        MLFLOW_TRACKING_PASSWORD=credentials["admin_password"],
    )
    client = MlflowClient(tracking_uri="http://127.0.0.1:18750")
    evidence = json.loads(REPORT.read_text()) if REPORT.exists() else {}

    def save():
        private_file(REPORT, json.dumps(evidence, indent=2) + "\n", replace=True)

    session = DemoSession()
    if "outage_run" not in evidence:
        previous = json.loads(
            Path("/opt/open-agent/control/step-content-verification.json").read_text()
        )
        # Only the owned observation backend is stopped. Control plane, model, worker and sandbox remain active.
        command("systemctl", "stop", "open-agent-mlflow.service")
        try:
            accepted = session.call(
                "/api/v1/agent-deployments/" + previous["deployment_id"] + "/runs",
                {
                    "input": {
                        "message": "读取 Skill 的 SKILL.md。实际使用工具创建 /workspace/outputs/观测恢复验收.txt，写入“观测后端停服期间，任务仍正常执行并保留资料”。再用工具读取验证。仅使用 Python 标准库，最后简短回复实际结果。",
                        "history": [],
                    },
                    "timeoutSeconds": 300,
                },
                headers={"Idempotency-Key": "mlflow-outage-contract-20261105"},
            )
            evidence["outage_run"] = accepted["run_id"]
            save()
            run = complete(session, evidence["outage_run"])
            assert run["state"] == "succeeded", run["state"]
            for _ in range(75):
                state = sql(
                    "SELECT last_error FROM oa_observation_outbox WHERE run_id='"
                    + evidence["outage_run"]
                    + "' AND revision>acked_revision;"
                ).strip()
                if state == "backend_unavailable":
                    break
                time.sleep(2)
            else:
                raise RuntimeError("No durable export failure recorded")
            evidence["task_succeeded_during_backend_outage"] = True
            save()
        finally:
            command("systemctl", "start", "open-agent-mlflow.service")
    identifier = evidence["outage_run"]
    for _ in range(150):
        pending = sql(
            "SELECT COUNT(*) FROM oa_observation_outbox WHERE run_id='"
            + identifier
            + "' AND revision>acked_revision;"
        ).strip()
        if pending == "0":
            break
        time.sleep(2)
    else:
        raise RuntimeError("Observation delivery did not recover")
    rows = []
    after = 0
    while True:
        page = session.call(
            f"/api/v1/runs/{identifier}/observations?after={after}&limit=100"
        )
        rows.extend(page["items"])
        if not page["hasMore"]:
            break
        after = page["next"]
    parts = (
        sql(
            "SET time_zone='+00:00'; SELECT a.tenant_id,a.environment,a.run_id,r.deployment_id,r.state,a.attempt_no,"
            "DATE_FORMAT(a.created_at,'%Y-%m-%dT%H:%i:%s.%fZ'),DATE_FORMAT(a.released_at,'%Y-%m-%dT%H:%i:%s.%fZ') "
            "FROM oa_run_attempt a JOIN oa_agent_run r ON a.run_id=r.run_id WHERE a.run_id='"
            + identifier
            + "';"
        )
        .strip()
        .split("\t")
    )
    assert len(parts) == 8
    run = dict(
        zip(
            [
                "tenant_id",
                "environment",
                "run_id",
                "deployment_id",
                "state",
                "attempt_no",
                "created_at",
                "released_at",
            ],
            parts,
        )
    )
    run["attempt_no"] = int(run["attempt_no"])
    spans = project(run, rows)
    trace_id = f"tr-{spans[0].context.trace_id:032x}"
    trace = client.get_trace(trace_id, display=False)
    actual = trace.data.spans
    by_step = {span.attributes.get("open_agent.step_id"): span for span in actual}
    for row in rows:
        if row["finish_json"] is None:
            continue
        span = by_step[row["start_json"]["stepId"]]
        if row["start_json"].get("content") is not None:
            assert span.inputs == row["start_json"]["content"]
        if row["finish_json"].get("content") is not None:
            assert span.outputs == row["finish_json"]["content"]
    model = [
        span for span in actual if span.attributes.get("open_agent.kind") == "model"
    ]
    expected = {
        "input_tokens": int(page["totals"]["inputTokens"]),
        "output_tokens": int(page["totals"]["outputTokens"]),
    }
    expected["total_tokens"] = sum(expected.values())
    assert trace.info.token_usage == expected, (trace.info.token_usage, expected)
    assert len(actual) == len(spans)
    assert len({s.span_id for s in actual}) == len(actual)
    first = (len(actual), dict(trace.info.token_usage))
    exporter = official_exporter(config["backend"])
    try:
        for _ in range(2):
            assert DurableExporter(None, exporter).send(spans)
    finally:
        exporter.shutdown()
    reread = client.get_trace(trace_id, display=False)
    assert first == (len(reread.data.spans), dict(reread.info.token_usage))
    evidence.update(
        trace_id=trace_id,
        steps=len(rows),
        spans=len(actual),
        model_calls=len(model),
        native_step_content_matches=True,
        usage=expected,
        duplicate_export_preserves_counts=True,
        delivery_attempts=int(
            sql(
                "SELECT delivery_attempts FROM oa_observation_outbox WHERE run_id='"
                + identifier
                + "';"
            ).strip()
        ),
        execution_attempts=int(
            sql(
                "SELECT COUNT(*) FROM oa_run_attempt WHERE run_id='" + identifier + "';"
            ).strip()
        ),
    )
    assert evidence["execution_attempts"] == 1
    outputs = session.call(f"/api/v1/runs/{identifier}/outputs")
    assert outputs and all(item["state"] == "available" for item in outputs)
    evidence["retained_files"] = len(outputs)
    other = DemoSession(
        "member-demo",
        json.loads(Path("/opt/open-agent/control/member-secrets.json").read_text())[
            "password"
        ],
        require_tenant=False,
    )
    raw(other, f"/api/v1/runs/{identifier}/observations", 404)
    evidence["other_tenant_denied"] = True
    # The export user is not a backend administrator and cannot read a second ungranted experiment.
    auth = get_app_client("basic-auth", tracking_uri="http://127.0.0.1:18750")
    assert auth.get_user(credentials["export_user"]).is_admin is False
    probe = client.get_experiment_by_name("open-agent-access-probe")
    probe_id = (
        probe.experiment_id
        if probe
        else client.create_experiment("open-agent-access-probe")
    )
    os.environ.update(
        MLFLOW_TRACKING_USERNAME=credentials["export_user"],
        MLFLOW_TRACKING_PASSWORD=credentials["export_password"],
    )
    limited = MlflowClient(tracking_uri="http://127.0.0.1:18750")
    try:
        limited.get_experiment(probe_id)
    except MlflowException as error:
        # Basic-auth returns a plain-text 403, wrapped by the official SDK as MlflowException.
        assert (
            error.error_code == "PERMISSION_DENIED"
            or "error code 403 != 200" in str(error)
        )
    else:
        raise AssertionError("Exporter has ungranted experiment access")
    evidence["export_identity_scope_verified"] = True
    for name in ["open-agent-mlflow.service", "open-agent-trace-exporter.service"]:
        value = command(
            "systemctl", "show", name, "-p", "MemoryCurrent", "--value"
        ).stdout.strip()
        evidence[name + "_memory_bytes"] = int(value)
    save()
    print(json.dumps(evidence), flush=True)


if __name__ == "__main__":
    main()
