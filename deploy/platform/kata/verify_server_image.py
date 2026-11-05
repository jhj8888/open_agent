"""Isolated actual-provider contract on a preloaded official OpenSandbox image."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import tarfile

ROOT = Path('/opt/open-agent/kata-profiles')
OWNER = 'open-agent.kata-profiles.v1'
IMAGE = 'm.daocloud.io/docker.io/opensandbox/server@sha256:68ca0212a2749b2c73096ce2ec0264455c64442c45f81007db442f52bf84c9d1'
FILES = {'manifest.json','server.toml','batchsandbox-template.yaml','network-policy.yaml',
         'namespace.yaml','service-account.yaml','resource-quota.yaml','verify_upstream_profile.py','upstream.json'}


def verify(archive: Path, expected: str):
    os.umask(0o077)
    assert sha256(archive.read_bytes()).hexdigest() == expected
    if not ROOT.exists():
        ROOT.mkdir(mode=0o700)
        (ROOT/'.owner').write_text(OWNER)
    assert not ROOT.is_symlink() and ROOT.parent.resolve() == Path('/opt/open-agent')
    assert (ROOT/'.owner').read_text() == OWNER
    folder = ROOT/expected
    if not folder.exists():folder.mkdir(mode=0o755)
    assert folder.is_dir() and not folder.is_symlink()
    folder.chmod(0o755)  # Public fixture only; the containing ROOT stays private.
    with tarfile.open(archive,'r:gz') as source:
        members = source.getmembers()
        assert len(members) == len(FILES) and {m.name for m in members} == FILES
        for member in members:
            assert member.isfile() and PurePosixPath(member.name).name == member.name and member.size <= 131072
            target = folder/member.name
            assert not target.is_symlink()
            data = source.extractfile(member).read()
            if target.exists():assert target.read_bytes() == data
            else:target.write_bytes(data)
            target.chmod(0o444)
    identity = json.loads(subprocess.check_output(['docker','image','inspect',IMAGE]))[0]
    result = subprocess.run(['docker','run','--rm','--pull=never','--network=none','--read-only',
        '--user=10001:10001','--cap-drop=ALL','--security-opt=no-new-privileges',
        '--memory=512m','--memory-swap=512m','--cpus=1','--pids-limit=128',
        '--tmpfs=/tmp:rw,nosuid,nodev,size=64m,mode=1777',
        '--mount',f'type=bind,source={folder},target=/etc/open-agent-sandbox,readonly',
        '--entrypoint=/app/.venv/bin/python',IMAGE,'/etc/open-agent-sandbox/verify_upstream_profile.py'],
        capture_output=True,text=True,timeout=120)
    (folder/'provider.log').write_text(result.stdout+result.stderr)
    if result.returncode:raise RuntimeError('Official provider validation failed; inspect '+str(folder/'provider.log'))
    records = [json.loads(line) for line in result.stdout.splitlines() if line.startswith('{"event": "upstream_profile_verified"')]
    assert len(records) == 1
    record = records[0]
    record.update(server_image=IMAGE,server_image_id=identity['Id'],input_sha256=expected,
        network='none',read_only=True,uid=10001,fixture_registry_references_only=True)
    (folder/'verification.json').write_text(json.dumps(record,indent=2)+'\n')
    (ROOT/'latest-verification.json').write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps({key:value for key,value in record.items() if key!='generated_workload'}),flush=True)
    return record


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--archive',type=Path,required=True)
    parser.add_argument('--expected',required=True)
    args=parser.parse_args()
    verify(args.archive,args.expected)
