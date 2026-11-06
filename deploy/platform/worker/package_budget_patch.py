"""Package reviewed model budget changes against the frozen demo source release."""
from hashlib import sha256
import io
import json
from pathlib import Path
import tarfile

ROOT=Path(__file__).resolve().parents[3]
BASE='54f202cc073d5a2991700be8ae9079ecd96b9aff21fbf98dc98fc9e1620a5653'
FILES=['core/agent/durable/control.py', 'core/agent/durable/executor.py', 'core/agent/durable/permit.py', 'core/agent/durable/runtime_config.py', 'core/agent/durable/model_budget.py', 'core/agent/engine/deepagents_runner.py', 'core/agent/tests/test_model_budget.py', 'core/agent/tests/test_durable_executor.py', 'core/agent/tests/test_durable_permit.py', 'core/agent/tests/test_durable_control.py', 'core/agent/tests/test_execution_observations.py', 'core/agent/tests/test_deepagents_harness.py']


def main():
    output=ROOT/'.local/budget-source-patch.tar'
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
    (ROOT/'.local/budget-source-patch.json').write_bytes((json.dumps(report,indent=2)+'\n').encode())
    print(json.dumps({'files':len(entries),'sha256':report['sha256']}))


if __name__=='__main__':main()
