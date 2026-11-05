"""Restart only the idle owned API and prove retained downloads survive sandbox cleanup."""

import json
import time
import urllib.request
from uuid import UUID

from provision_data import (
    ROOT,
    DATABASE,
    OWNER,
    LABEL,
    command,
    installation_lock,
    private_file,
)
from verify_task_outputs import main as verify, REPORT


def sql(query):
    return command(
        "docker",
        "exec",
        "-i",
        "open-agent-control-mysql",
        "sh",
        "-c",
        'MYSQL_PWD="$(cat /run/secrets/mysql-root)" mysql -N -uroot ' + DATABASE,
        input=query,
    ).stdout.strip()


def main():
    before = json.loads(REPORT.read_text())
    ids = ",".join("'" + str(UUID(value)) + "'" for value in before["runs"].values())
    for _ in range(45):
        if (
            sql(
                "SELECT COUNT(*) FROM oa_run_attempt WHERE run_id IN ("
                + ids
                + ") AND lease_state<>'released';"
            )
            == "0"
        ):
            break
        time.sleep(2)
    else:
        raise RuntimeError("Output task execution cleanup is pending")
    assert (
        sql(
            "SELECT COUNT(*) FROM oa_run_sandbox WHERE run_id IN ("
            + ids
            + ") AND phase<>'released';"
        )
        == "0"
    )
    containers = command(
        "docker", "ps", "-aq", "--filter", "label=oa.platform=open-agent"
    ).stdout.split()
    if containers:
        inventory = json.loads(command("docker", "inspect", *containers).stdout)
        assert all(
            item["Config"].get("Labels", {}).get("oa.run")
            not in before["runs"].values()
            for item in inventory
        )
    with installation_lock():
        assert (
            sql(
                "SELECT COUNT(*) FROM oa_agent_run WHERE state IN ('queued','running','cancel_requested');"
            )
            == "0"
        )
        container = json.loads(
            command("docker", "inspect", "open-agent-control-api").stdout
        )[0]
        assert container["Config"]["Labels"].get(LABEL) == OWNER
        command("docker", "restart", "open-agent-control-api", timeout=90)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(60):
            try:
                with opener.open(
                    "http://127.0.0.1:18082/health/ready", timeout=2
                ) as response:
                    if response.status == 200:
                        break
            except Exception:
                pass
            time.sleep(1)
        else:
            raise RuntimeError("Owned API readiness timeout after restart")
    verify()
    after = json.loads(REPORT.read_text())
    assert before["outputs"] == after["outputs"]
    after.update(api_restart_verified=True, physical_sandbox_cleanup_verified=True)
    private_file(
        REPORT, json.dumps(after, ensure_ascii=False, indent=2) + "\n", replace=True
    )
    print(
        "Same output IDs and digests survived API restart and physical sandbox cleanup",
        flush=True,
    )


if __name__ == "__main__":
    main()
