"""Exercise the production snapshot helper inside a fresh, networkless owned gVisor container."""

import hashlib
import json
from pathlib import Path
import subprocess
import uuid

from provision_data import command

TEST = r"""
import hashlib,json,os,pathlib,shutil,subprocess,sys,uuid
root=pathlib.Path('/workspace/outputs')
root.mkdir();os.chown(root,1000,1000)
def run(ok):
    token=uuid.uuid4().hex
    result=subprocess.run([sys.executable,'-I','/helper.py',token],capture_output=True,timeout=20)
    assert (result.returncode==0)==ok, result.stderr.decode()[-300:]
    if ok:
        rows=json.loads(result.stdout)['files']
        for row in rows:
            data=pathlib.Path(row['snapshot']).read_bytes()
            assert len(data)==row['size'] and hashlib.sha256(data).hexdigest()==row['sha256']
        path=pathlib.Path('/opt/open-agent/outputs')/token
        if path.exists():shutil.rmtree(path)
        return rows
    assert not (pathlib.Path('/opt/open-agent/outputs')/token).exists()
def reset():
    for path in root.iterdir():
        if path.is_dir() and not path.is_symlink():shutil.rmtree(path)
        else:path.unlink()
def write(name,data):
    path=root/name;path.write_bytes(data);os.chown(path,1000,1000);return path
rows=run(True);assert rows==[]
write('资料.csv','项目,完成\nA,是'.encode());write('binary.pdf',b'%PDF-1.4\n'+bytes(range(256))*4097)
assert len(run(True))==2;reset()
(root/'escape').symlink_to('/etc/shadow');run(False);reset()
directory=root/'directory';directory.symlink_to('/etc',target_is_directory=True);run(False);reset()
one=write('one',b'content');os.link(one,root/'hard');run(False);reset()
os.mkfifo(root/'fifo');os.chown(root/'fifo',1000,1000);run(False);reset()
path=write('huge',b'');os.truncate(path,32*1024*1024+1);run(False);reset()
for index in range(33):write(str(index),b'')
run(False);reset()
write('new\nline',b'x');run(False);reset()
write('secret',b'root-only').chmod(0o000);run(False);reset()
write('empty',b'');rows=run(True);assert len(rows)==1 and rows[0]['size']==0
print(json.dumps({'checks':10,'binary_bytes':256*4097+9,'root_secret_unreadable':True,'network':'none'}))
"""


def main():
    helper = Path(__file__).with_name("output_snapshot.py")
    assert helper.is_file()
    config = json.loads(Path("/opt/open-agent/sandbox/deployment.json").read_text())
    image = config["runtime_image"]
    assert "@sha256:" in image
    name = "open-agent-output-verify-" + uuid.uuid4().hex[:12]
    try:
        result = command(
            "docker",
            "run",
            "--rm",
            "--name",
            name,
            "--label",
            "open-agent.verification=task-output",
            "--network",
            "none",
            "--runtime",
            "open-agent-runsc",
            "--read-only",
            "--memory",
            "512m",
            "--cpus",
            "0.5",
            "--pids-limit",
            "64",
            "--tmpfs",
            "/workspace:rw,nosuid,nodev,size=64m,mode=755",
            "--tmpfs",
            "/opt/open-agent:rw,nosuid,nodev,size=64m,mode=755",
            "--mount",
            "type=bind,src=" + str(helper) + ",dst=/helper.py,readonly",
            "--entrypoint",
            "/usr/local/bin/python3",
            image,
            "-I",
            "-c",
            TEST,
            timeout=120,
            check=False,
        )
        if result.returncode:
            # This command has only the public fixture and no credentials.
            raise RuntimeError(result.stderr[-2000:])
        report = json.loads(result.stdout)
        report["helper_sha256"] = hashlib.sha256(helper.read_bytes()).hexdigest()
        Path("/opt/open-agent/verification/output-snapshot.json").write_text(
            json.dumps(report, indent=2) + "\n"
        )
        print(json.dumps(report), flush=True)
    finally:
        # This randomly named verification container is the only possible cleanup target.
        if (
            subprocess.run(["docker", "inspect", name], capture_output=True).returncode
            == 0
        ):
            command("docker", "rm", "-f", name)


if __name__ == "__main__":
    main()
