"""Run real Linux reader races in an existing fixed, network-disabled worker image."""
from hashlib import sha256
import json
from pathlib import Path
import subprocess
from datetime import datetime, timezone

from provision_data import ROOT, private_file


def main():
    build = json.loads(Path('/opt/open-agent/worker-image-build/build-report.json').read_bytes())
    image = build['image']['local_image_id']
    source_sha256 = '7612f6edb43740eea034f6b76e9afa1796d1bdb28dbae2433e06ce0f1628c97e'
    assert image == 'sha256:c87a7579793fa109b525ed2a6c2f3d1d1b95e43c142b33bd8f84fd59b4c58410'
    folder = Path(__file__).resolve().parent
    test = folder / 'test_output_snapshot_races.py'
    test.chmod(0o444)
    # No writable host directories, tenant content or credentials are mounted.
    result = subprocess.run(['docker','run','--rm','--network=none','--read-only','--user=10001:10001',
        '--cap-drop=ALL','--security-opt=no-new-privileges','--memory=256m','--memory-swap=256m',
        '--cpus=1','--pids-limit=64','--tmpfs=/tmp:rw,nosuid,nodev,size=64m,mode=1777',
        '--mount','type=bind,source='+str(test)+',target=/contract/test_output_snapshot_races.py,readonly',
        '--env=OUTPUT_SNAPSHOT_SOURCE=/opt/open-agent/core/agent/platform_runtime/output_snapshot.py',
        '--env=OUTPUT_SNAPSHOT_SHA256='+source_sha256,
        image,'-m','pytest','/contract/test_output_snapshot_races.py','-q','-p','no:cacheprovider'],
        capture_output=True,text=True,timeout=120)
    private_file(ROOT/'output-races.log',result.stdout+result.stderr,replace=True)
    if result.returncode: raise RuntimeError('output_reader_races_failed')
    assert '4 passed' in result.stdout
    evidence = {'checked_at':datetime.now(timezone.utc).isoformat(),'image':image,
        'test_sha256':sha256(test.read_bytes()).hexdigest(),'source_sha256':source_sha256,'tests_passed':4,'mutating_iterations':30,
        'mutations':['append','truncate','overwrite_restore_mtime'],'real_linux_pipe_and_files':True,
        'network':'none','uid':10001,'read_only_container':True,'capabilities':[],
        'stable_content_sha256_verified':True,'production_code_changed':False,
        'scope':'Exact production UID-reader logic with fixed input root redirected to private test folder; not full privileged-parent/SDK/Kata verification.'}
    private_file(ROOT/'output-races-verification.json',json.dumps(evidence,indent=2)+'\n',replace=True)
    print(json.dumps(evidence),flush=True)


if __name__ == '__main__': main()
