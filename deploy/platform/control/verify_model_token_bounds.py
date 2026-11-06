"""Read-only check of the disabled model-bound deployment and existing model lists."""
from datetime import datetime, timezone
from hashlib import sha256
import json
import zipfile

from demo_session import DemoSession
from provision_api import ARTIFACT_SHA256
from provision_data import ROOT, private_file
from verify_period_reports import sql
from verify_token_budget_ledger import main as verify_ledger


def main():
    verify_ledger()
    jar=ROOT/'releases'/ARTIFACT_SHA256/'platform.jar'
    with zipfile.ZipFile(jar) as archive:
        migration=archive.read('BOOT-INF/classes/db/migration/V1.71__bind_model_token_contracts.sql')
        assert 'BOOT-INF/classes/com/iflytek/astron/console/hub/platform/model/ModelTokenBoundPolicy.class' in archive.namelist()
    assert sql("SELECT COUNT(*) FROM flyway_schema_history WHERE version='1.71' AND success=1;").strip()=='1'
    assert sql('SELECT COUNT(*) FROM oa_model_token_bound;').strip()=='0'
    before=sql('SELECT COUNT(*) FROM oa_agent_run;').strip()
    session=DemoSession();session.api='https://120.26.81.83'
    models=session.call(session.management+'/models')
    assert models
    for model in models:
        assert set(('max_total_tokens','token_bound_source','token_bound_contract_sha256','token_bound_valid_until_ms'))<=model.keys()
        assert model['max_total_tokens'] is None and model['token_bound_contract_sha256'] is None
        assert not set(('credential_cipher','credential_nonce','apiKey','credential'))&model.keys()
    assert before==sql('SELECT COUNT(*) FROM oa_agent_run;').strip()
    report={'checked_at':datetime.now(timezone.utc).isoformat(),'read_only':True,'schema_version':'1.72',
            'api_artifact_sha256':ARTIFACT_SHA256,'migration_sha256':sha256(migration).hexdigest(),
            'existing_models_readable':len(models),'legacy_models_not_backfilled':True,
            'model_secrets_not_returned':True,'business_task_count_unchanged':True,
            'token_budget_enforced':False,'real_provider_contract_approved':False}
    private_file(ROOT/'model-token-bounds-verification.json',json.dumps(report,indent=2)+'\n',replace=True)
    print(json.dumps(report),flush=True)


if __name__=='__main__':
    main()
