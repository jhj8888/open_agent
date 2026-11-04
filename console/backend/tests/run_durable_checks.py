"""Run real Java ledger tests in an owned MySQL container, including crash recovery.

Requires Docker, Java 21 and Maven. Preload MYSQL_TEST_IMAGE for offline execution.
Only an ephemeral loopback port is published; no existing database is accepted.
"""
import os
from pathlib import Path
import secrets
import socket
import subprocess
import tempfile
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[3]
IMAGE = os.environ.get("MYSQL_TEST_IMAGE", "mysql@sha256:a81d043930e51745522bd5c430961cf8286f0a23f0a9a2a6e0108f7ea71b0902")


def docker(*args, check=True):
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=120)
    if check and result.returncode:
        raise RuntimeError("Verification Docker operation failed: " + result.stderr[:1000])
    return result


def main():
    name = "open-agent-run-verification-" + uuid.uuid4().hex[:12]
    docker("image", "inspect", IMAGE)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="open-agent-run-test-") as directory:
        private = Path(directory)
        password = secrets.token_urlsafe(40)
        env_file = private / "mysql.env"
        env_file.write_text("MYSQL_ROOT_PASSWORD=" + password + "\nMYSQL_ROOT_HOST=%\n", encoding="utf-8")
        env_file.chmod(0o600)
        signal = private / "restart.signal"
        signal.write_text("idle", encoding="utf-8")
        stop = threading.Event()
        errors = []
        created = False
        watcher = None

        def ready():
            for _ in range(100):
                result = docker("exec", name, "sh", "-c",
                                'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql --protocol=TCP -h127.0.0.1 -uroot -N -e "SELECT 1"', check=False)
                if result.returncode == 0:
                    return
                time.sleep(1)
            raise RuntimeError("Owned verification database did not become ready")

        def restart():
            try:
                while not stop.wait(0.1):
                    if signal.read_text(encoding="utf-8") == "restart":
                        docker("kill", "--signal=KILL", name)
                        docker("start", name)
                        ready()
                        signal.write_text("ready", encoding="utf-8")
            except Exception as error:
                errors.append(type(error).__name__)
                signal.write_text("failed", encoding="utf-8")

        try:
            docker("run", "--detach", "--name", name, "--label", "open-agent.verification=run-ledger",
                   "--memory", "768m", "--cpus", "1", "--pids-limit", "256",
                   "-p", f"127.0.0.1:{port}:3306", "--env-file", str(env_file), IMAGE, "--skip-name-resolve")
            created = True
            ready()
            watcher = threading.Thread(target=restart, daemon=True)
            watcher.start()
            env = dict(os.environ,
                       OA_RUN_TEST_JDBC_URL=f"jdbc:mysql://127.0.0.1:{port}/?useSSL=false&allowPublicKeyRetrieval=true&serverTimezone=UTC&connectTimeout=10000&socketTimeout=30000",
                       OA_RUN_TEST_DB_USER="root", OA_RUN_TEST_DB_PASSWORD=password,
                       OA_RUN_TEST_RESTART_SIGNAL=str(signal))
            result = subprocess.run(["mvn", "-B", "-ntp", "-f", "console/backend/pom.xml", "-pl", "hub", "-am",
                                     "-Dtest=Run*Test", "-Dsurefire.failIfNoSpecifiedTests=false", "test"],
                                    cwd=ROOT, env=env, timeout=900)
            if errors:
                raise RuntimeError("Owned database crash/restart verification failed")
            return result.returncode
        finally:
            stop.set()
            if watcher:
                watcher.join(timeout=120)
            if created:
                label = docker("inspect", "--format", '{{index .Config.Labels "open-agent.verification"}}', name).stdout.strip()
                if label != "run-ledger":
                    raise RuntimeError("Refusing to remove an unrelated database")
                docker("rm", "-f", "-v", name)


if __name__ == "__main__":
    raise SystemExit(main())
