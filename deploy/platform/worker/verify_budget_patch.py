"""Verify the staged release with the frozen Linux interpreter."""
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

ROOT=Path('/opt/open-agent/agent-worker')
PYTHON=str(ROOT/'venv/bin/python')


def main():
    record=json.loads((ROOT/'staged-budget-release.json').read_text())
    source=Path(record['source'])
    if source.is_symlink() or source!=ROOT/'releases'/record['source_sha256'][:16]:
        raise RuntimeError('budget_verification_source_invalid')
    for entry in record['files']:
        if sha256((source/entry['path']).read_bytes()).hexdigest()!=entry['sha256']:
            raise RuntimeError('budget_verification_source_changed')
    active=(ROOT/'deployment.json').read_bytes()
    version_code="import importlib.metadata as m,json; print(json.dumps({p:m.version(p) for p in ('temporalio','deepagents','httpx','aiohttp','langchain-openai')}))"
    versions=json.loads(subprocess.check_output([PYTHON,'-c',version_code],text=True))
    report_dir=ROOT/'budget-verification';report_dir.mkdir(exist_ok=True,mode=0o700)
    xml=report_dir/'tests.xml'
    environment={**os.environ,'PYTHONPATH':str(source/'core')+':'+str(source/'core/common'),
        'PYTHONDONTWRITEBYTECODE':'1','LANGSMITH_TRACING':'false','LANGCHAIN_TRACING_V2':'false',
        'TEMPORAL_TEST_BINARY':'/opt/open-agent/runtime/temporal/1.10.0/temporal'}
    tests=['test_model_budget.py', 'test_durable_executor.py', 'test_durable_permit.py', 'test_durable_control.py', 'test_execution_observations.py', 'test_deepagents_harness.py']
    result=subprocess.run([PYTHON,'-m','pytest',*[str(source/'core/agent/tests'/name) for name in tests],
        '-q','--tb=short','-p','no:cacheprovider','--junitxml='+str(xml)],env=environment,
        stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=240,cwd=str(report_dir))
    (report_dir/'pytest.log').write_bytes(result.stdout)
    if result.returncode:raise RuntimeError('budget_staged_tests_failed')
    suites=ET.parse(xml).getroot().findall('testsuite')
    if any(int(s.attrib[k]) for s in suites for k in ('failures','errors','skipped')):
        raise RuntimeError('budget_staged_tests_incomplete')
    after=json.loads(subprocess.check_output([PYTHON,'-c',version_code],text=True))
    if versions!=after or active!=(ROOT/'deployment.json').read_bytes():
        raise RuntimeError('budget_staged_check_changed_live_runtime')
    report={'checked_at':datetime.now(timezone.utc).isoformat(),'source_sha256':record['source_sha256'],
            'tests_passed':sum(int(s.attrib['tests']) for s in suites),'failures':0,'errors':0,'skipped':0,
            'model_budget_callback_and_sdk_verified':True,'real_model_provider':False,'versions':versions,
            'dependencies_unchanged':True,'active_service_changed':False,
            'cases':[case.attrib['name'] for s in suites for case in s.findall('testcase')]}
    (report_dir/'verification.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='cases'}))


if __name__=='__main__':main()
