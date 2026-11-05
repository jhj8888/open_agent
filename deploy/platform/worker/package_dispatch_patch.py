"""Package reviewed dispatcher changes against the frozen demo source release."""
from hashlib import sha256
import io
import json
from pathlib import Path
import tarfile

ROOT=Path(__file__).resolve().parents[3]
BASE='8c64a5475a783efd607f40d86f62e9f30967609309603497f561bd8358fbb7a0'
FILES=['core/agent/durable/control.py','core/agent/durable/dispatcher.py','core/agent/durable/service.py',
       'core/agent/tests/test_dispatch_environment.py','core/agent/tests/test_durable_control.py',
       'core/agent/tests/test_durable_workflow.py']


def main():
    output=ROOT/'.local/dispatch-source-patch.tar'
    entries=[]
    with tarfile.open(output,'w') as archive:
        for name in FILES:
            data=(ROOT/name).read_bytes()
            info=tarfile.TarInfo(name);info.size=len(data);info.mode=0o644;info.mtime=0
            archive.addfile(info,io.BytesIO(data))
            entries.append({'path':name,'sha256':sha256(data).hexdigest(),'bytes':len(data)})
    report={'base_source_sha256':BASE,'sha256':sha256(output.read_bytes()).hexdigest(),
            'files':entries,'dependency_changes':False,
            'scope':'Frozen demo runtime source patch; independent from the current cluster SDK dependency graph'}
    (ROOT/'.local/dispatch-source-patch.json').write_bytes((json.dumps(report,indent=2)+'\n').encode())
    print(json.dumps({'files':len(entries),'sha256':report['sha256']}))


if __name__=='__main__':main()
