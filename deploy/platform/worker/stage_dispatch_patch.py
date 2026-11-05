"""Stage a new immutable source release; do not alter the active service or venv."""
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import re
import tarfile

ROOT=Path('/opt/open-agent/agent-worker')
SOURCE=Path(__file__).resolve().parent
ALLOWED={'core/agent/durable/control.py','core/agent/durable/dispatcher.py','core/agent/durable/service.py',
         'core/agent/tests/test_dispatch_environment.py','core/agent/tests/test_durable_control.py',
         'core/agent/tests/test_durable_workflow.py'}


def main():
    os.umask(0o022)
    metadata=json.loads((SOURCE/'dispatch-source-patch.json').read_text())
    installed=json.loads((ROOT/'deployment.json').read_text())
    baseline=Path(installed['source'])
    base_digest=metadata['base_source_sha256']
    if (installed['source_sha256']!=base_digest or not re.fullmatch('[a-f0-9]{64}',base_digest)
            or baseline!=ROOT/'releases'/base_digest[:16] or baseline.is_symlink()
            or (baseline/'.source-sha256').read_text().strip()!=base_digest):
        raise RuntimeError('dispatch_patch_baseline_mismatch')
    raw=(SOURCE/'dispatch-source-patch.tar').read_bytes()
    if sha256(raw).hexdigest()!=metadata['sha256'] or {f['path'] for f in metadata['files']}!=ALLOWED:
        raise RuntimeError('dispatch_patch_manifest_invalid')
    payloads={}
    for path in baseline.rglob('*'):
        if path.is_symlink():raise RuntimeError('dispatch_patch_linked_source')
        if path.is_file() and (path.name in {'LICENSE','NOTICE'} or path.suffix in {'.py','.toml','.lock','.md'}):
            if path.stat().st_size>8*1024*1024:raise RuntimeError('dispatch_patch_source_limit')
            payloads[path.relative_to(baseline).as_posix()]=path.read_bytes()
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        members=archive.getmembers()
        if {m.name for m in members}!=ALLOWED or len(members)!=len(ALLOWED):
            raise RuntimeError('dispatch_patch_archive_invalid')
        for member in members:
            expected=next(f for f in metadata['files'] if f['path']==member.name)
            if not member.isfile() or member.size!=expected['bytes'] or member.size>256*1024:
                raise RuntimeError('dispatch_patch_member_invalid')
            data=archive.extractfile(member).read()
            if sha256(data).hexdigest()!=expected['sha256']:raise RuntimeError('dispatch_patch_member_digest')
            payloads[member.name]=data
    archives=ROOT/'staged-archives'
    if archives.is_symlink():raise RuntimeError('dispatch_patch_archive_path_invalid')
    archives.mkdir(exist_ok=True,mode=0o700)
    temporary=archives/(metadata['sha256']+'.tar')
    with tarfile.open(temporary,'w') as archive:
        for name,data in sorted(payloads.items()):
            info=tarfile.TarInfo(name);info.size=len(data);info.mode=0o644;info.mtime=0
            archive.addfile(info,io.BytesIO(data))
    digest=sha256(temporary.read_bytes()).hexdigest()
    candidate=ROOT/'releases'/digest[:16]
    if candidate.is_symlink():raise RuntimeError('dispatch_patch_release_link')
    if candidate.exists():
        if (candidate/'.source-sha256').read_text().strip()!=digest:
            raise RuntimeError('dispatch_patch_release_conflict')
        for name,data in payloads.items():
            if (candidate/name).is_symlink() or (candidate/name).read_bytes()!=data:
                raise RuntimeError('dispatch_patch_release_changed')
    else:
        candidate.mkdir(mode=0o755)
        for name,data in payloads.items():
            target=candidate/name;target.parent.mkdir(parents=True,exist_ok=True,mode=0o755)
            target.write_bytes(data);target.chmod(0o644)
        (candidate/'.source-sha256').write_text(digest+'\n')
    report={'source':str(candidate),'source_sha256':digest,'base_source_sha256':base_digest,
            'patch_sha256':metadata['sha256'],'files':metadata['files'],'full_source_files':len(payloads),
            'archive':str(temporary),'environment':installed['environment'],'versions':installed['versions'],
            'dependency_changes':False,'active_service_changed':False}
    (ROOT/'staged-dispatch-release.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:report[k] for k in ('source','source_sha256','full_source_files','dependency_changes','active_service_changed')}))


if __name__=='__main__':main()
