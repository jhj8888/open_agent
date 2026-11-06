"""Verify installed inactive task-budget wiring, without creating budget or model calls."""
from datetime import datetime, timezone
from hashlib import sha256
import json
import urllib.error
import urllib.request
import zipfile

from provision_api import ARTIFACT_SHA256
from provision_data import ROOT, private_file
from verify_model_token_bounds import main as verify_bounds
from verify_period_reports import sql


def main():
    verify_bounds()
    jar=ROOT/'releases'/ARTIFACT_SHA256/'platform.jar'
    with zipfile.ZipFile(jar) as archive:
        migration=archive.read('BOOT-INF/classes/db/migration/V1.72__bind_run_model_token_calls.sql')
        for name in ('RunModelBudgetService','PlatformRunModelBudgetController'):
            assert 'BOOT-INF/classes/com/iflytek/astron/console/hub/platform/run/'+name+'.class' in archive.namelist()
    assert sql("SELECT COUNT(*) FROM flyway_schema_history WHERE version='1.72' AND success=1;").strip()=='1'
    before=sql('SELECT COUNT(*) FROM oa_agent_run;').strip()
    for table in ('oa_run_model_token_call','oa_model_token_reservation','oa_model_token_day','oa_model_token_bound'):
        assert sql('SELECT COUNT(*) FROM '+table+';').strip()=='0'
    statuses={}
    for operation in ('prepare','dispatch','cancel','unknown','settle'):
        url='https://120.26.81.83/internal/platform/worker/runs/00000000-0000-0000-0000-000000000000/model-calls/'+operation
        request=urllib.request.Request(url,data=b'{}',headers={'Content-Type':'application/json'},method='POST')
        try:
            with urllib.request.urlopen(request,timeout=15) as response:status=response.status
        except urllib.error.HTTPError as error:status=error.code
        assert status in (401,403,404), 'worker_budget_route_publicly_accessible'
        statuses[operation]=status
    assert before==sql('SELECT COUNT(*) FROM oa_agent_run;').strip()
    report={'checked_at':datetime.now(timezone.utc).isoformat(),'read_only':True,'schema_version':'1.72',
            'api_artifact_sha256':ARTIFACT_SHA256,'migration_sha256':sha256(migration).hexdigest(),
            'public_worker_routes_denied':statuses,'business_task_count_unchanged':True,
            'budget_tables_empty':True,'token_budget_enforced':False,'real_provider_contract_approved':False}
    private_file(ROOT/'run-model-budget-verification.json',json.dumps(report,indent=2)+'\n',replace=True)
    print(json.dumps(report),flush=True)


if __name__=='__main__':main()
