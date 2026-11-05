"""Upgrade the owned idle demo API and source-only dispatcher together, with private backups."""
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from uuid import uuid4

sys.path.insert(0,'/opt/open-agent/recovery')
import recovery as r
sys.path.insert(0,'/opt/open-agent/verification/control-package')
import provision_api
from provision_data import ROOT

WORKER=Path('/opt/open-agent/agent-worker')
SUPERVISOR=Path('/opt/open-agent/supervisor')
UNIT=Path('/etc/systemd/system/open-agent-supervisor.service')
OWNER='open_agent.control.upgrade.v1'


def ready(source):
    for _ in range(60):
        pid=r.run('systemctl','show','open-agent-supervisor.service','-p','MainPID','--value').stdout.strip()
        path=SUPERVISOR/'ready.json'
        if path.exists():
            state=json.loads(path.read_text())
            if str(state['pid'])==pid and state['source']==source:return
        time.sleep(1)
    raise RuntimeError('upgraded_supervisor_not_ready')


os.umask(0o077)
with r.lock():
    r.installation();r.assert_idle();r.inspect('open-agent-control-api')
    assert (SUPERVISOR/'.owner').read_text().strip()=='open_agent.supervisor.demo.v1'
    candidate=json.loads((WORKER/'staged-dispatch-release.json').read_text())
    verification=json.loads((WORKER/'dispatch-verification/verification.json').read_text())
    assert verification['source_sha256']==candidate['source_sha256'] and verification['tests_passed']>=59
    assert verification['failures']==verification['errors']==verification['skipped']==0
    for entry in candidate['files']:
        assert r.digest(Path(candidate['source'])/entry['path'])==entry['sha256']
    old_worker=json.loads((WORKER/'deployment.json').read_text())
    assert old_worker['source_sha256']==candidate['base_source_sha256']
    old_source=old_worker['source'];new_source=candidate['source']
    assert re.fullmatch(r'/opt/open-agent/agent-worker/releases/[a-f0-9]{16}',new_source)
    assert not Path(new_source).is_symlink()
    files={'worker-deployment.json':WORKER/'deployment.json','supervisor-service.json':SUPERVISOR/'service.json',
           'supervisor-deployment.json':SUPERVISOR/'deployment.json','supervisor-unit.service':UNIT}
    originals={name:path.read_text() for name,path in files.items()}
    admission_path=ROOT/'execution-admission.json'
    assert not admission_path.is_symlink() and not admission_path.stat().st_mode&0o077
    admission_original=admission_path.read_text()
    assert json.loads(admission_original)=={'enabled':True,'supervisor':'demo-supervisor'}
    assert '# open_agent.supervisor.demo.v1' in originals['supervisor-unit.service']
    assert originals['supervisor-unit.service'].count(old_source)==3
    configuration=json.loads(originals['supervisor-service.json']);assert configuration['source']==old_source
    old_api=json.loads((ROOT/'api-deployment.json').read_text())['artifact_sha256']
    assert r.digest(ROOT/'releases'/provision_api.ARTIFACT_SHA256/'platform.jar')==provision_api.ARTIFACT_SHA256
    backups=ROOT/'upgrade-backups';assert (backups/'.owner').read_text().strip()==OWNER
    assert not backups.is_symlink() and not backups.stat().st_mode&0o077
    backup=backups/('pre-env-v164-'+uuid4().hex);backup.mkdir(mode=0o700)
    for name,value in originals.items():r.private(backup/name,value)
    r.private(backup/'execution-admission.json',admission_original)
    journal=ROOT/'upgrade-maintenance.json'
    if journal.exists():assert json.loads(journal.read_text())['phase'] in {'complete','rolled_back'}
    state={'owner':OWNER,'phase':'prepared','old_artifact':old_api,'new_artifact':provision_api.ARTIFACT_SHA256,
        'old_worker_source':old_source,'new_worker_source':new_source,'worker_source_sha256':candidate['source_sha256'],
        'started_at':r.utc(),'backup_directory':str(backup)}
    r.save(journal,state);started=time.monotonic()
    try:
        r.run('docker','stop','--time','30','open-agent-control-api',timeout=45)
        r.assert_idle()
        admission_path.unlink()  # New API boots closed until both dispatcher queues are ready.
        r.run('systemctl','stop','open-agent-supervisor.service',timeout=130)
        dump=r.run('docker','exec','open-agent-control-mysql','sh','-c',
            'MYSQL_PWD="$(cat /run/secrets/mysql-root)" mysqldump -uroot --single-transaction --routines --triggers '
            '--hex-blob --set-gtid-purged=OFF --databases astron_console',timeout=180).stdout
        r.private(backup/'database.sql',dump)
        state.update(phase='backed_up',backup_sha256=hashlib.sha256(dump.encode()).hexdigest());del dump
        r.save(journal,state)
        configuration.update(source=new_source,environment='test',production_task_queue='open-agent-demo-prod-v1')
        r.save(SUPERVISOR/'service.json',configuration)
        r.private(UNIT,originals['supervisor-unit.service'].replace(old_source,new_source));UNIT.chmod(0o644)
        worker={**old_worker,'source':new_source,'source_sha256':candidate['source_sha256'],
                'source_patch_base':candidate['base_source_sha256'],'service_started':True}
        r.save(WORKER/'deployment.json',worker)
        supervisor=json.loads(originals['supervisor-deployment.json'])
        supervisor.update(source=new_source,source_sha256=candidate['source_sha256'],environment='test')
        r.save(SUPERVISOR/'deployment.json',supervisor)
        provision_api.main()
        assert r.sql("SELECT version FROM flyway_schema_history WHERE success=1 ORDER BY installed_rank DESC LIMIT 1;")=='1.64'
        r.run('systemctl','daemon-reload');r.run('systemctl','start','open-agent-supervisor.service')
        ready(new_source)
        assert json.loads((SUPERVISOR/'ready.json').read_text())['task_queues']=={'test':'open-agent-demo-v1','prod':'open-agent-demo-prod-v1'}
        r.private(admission_path,admission_original)
        provision_api.main()
        state.update(phase='complete',finished_at=r.utc(),elapsed_seconds=round(time.monotonic()-started,3),
                     dispatcher_environments=['test','prod'],dependencies_changed=False)
        r.save(journal,state)
        print(json.dumps({k:state[k] for k in ('phase','new_artifact','worker_source_sha256','elapsed_seconds','dispatcher_environments')}))
    except BaseException:
        state['phase']='rolling_back';r.save(journal,state)
        r.run('systemctl','stop','open-agent-supervisor.service',timeout=130,check=False)
        admission_path.unlink(missing_ok=True)
        for name,path in files.items():r.private(path,originals[name])
        UNIT.chmod(0o644)
        provision_api.ARTIFACT_SHA256=old_api;provision_api.main()
        r.run('systemctl','daemon-reload');r.run('systemctl','start','open-agent-supervisor.service');ready(old_source)
        r.private(admission_path,admission_original);provision_api.main()
        state.update(phase='rolled_back',finished_at=r.utc());r.save(journal,state)
        raise
