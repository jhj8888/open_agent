"""Read-only deployed migration/feature-gate check; not an enforcement E2E claim."""
from datetime import datetime, timezone
from hashlib import sha256
import json
import zipfile

from demo_session import DemoSession
from provision_api import ARTIFACT_SHA256
from provision_data import ROOT, private_file
from verify_period_reports import sql


def main():
    deployment=json.loads((ROOT/'api-deployment.json').read_text())
    assert deployment['artifact_sha256']==ARTIFACT_SHA256
    jar=ROOT/'releases'/ARTIFACT_SHA256/'platform.jar'
    assert sha256(jar.read_bytes()).hexdigest()==ARTIFACT_SHA256
    with zipfile.ZipFile(jar) as archive:
        migration=archive.read('BOOT-INF/classes/db/migration/V1.70__reserve_model_token_budgets.sql')
        assert 'BOOT-INF/classes/com/iflytek/astron/console/hub/platform/run/TokenBudgetLedger.class' in archive.namelist()
    assert sql('SELECT version FROM flyway_schema_history WHERE success=1 ORDER BY installed_rank DESC LIMIT 1;').strip()=='1.71'
    assert sql("SELECT COUNT(*) FROM flyway_schema_history WHERE version='1.70' AND success=1;").strip()=='1'
    for table in ('oa_model_token_day','oa_model_token_reservation'):
        assert sql('SELECT COUNT(*) FROM '+table+';').strip()=='0', 'budget_unexpectedly_active'
    before=sql('SELECT COUNT(*) FROM oa_agent_run;').strip()
    session=DemoSession();session.api='https://120.26.81.83'
    quota=session.call(session.management+'/quotas')
    assert quota['tokenBudgetEnforced'] is False and 'dailyTaskTokens' in quota
    assert before==sql('SELECT COUNT(*) FROM oa_agent_run;').strip()
    report={'checked_at':datetime.now(timezone.utc).isoformat(),'read_only':True,
            'api_artifact_sha256':ARTIFACT_SHA256,'migration_sha256':sha256(migration).hexdigest(),
            'schema_version':'1.71','successful_migration_rows':1,'ledger_tables_empty':True,
            'token_budget_enforced':False,'existing_daily_task_metering_present':True,
            'business_task_count_unchanged':True,'enforcement_end_to_end_verified':False}
    private_file(ROOT/'token-budget-ledger-verification.json',json.dumps(report,indent=2)+'\n',replace=True)
    print(json.dumps(report),flush=True)


if __name__=='__main__':
    main()
