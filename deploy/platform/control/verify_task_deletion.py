"""Delete only a newly created, journaled synthetic demo task through public HTTPS."""
from datetime import datetime, timezone
import json
import time
import urllib.error
import urllib.request
from uuid import UUID, uuid4

from demo_session import DemoSession
from provision_data import ROOT, private_file


def main():
    session=DemoSession(api='https://120.26.81.83')
    journal=ROOT/'task-deletion-fixture.json'
    owner='open_agent.task_deletion.fixture.v1'
    deployment=json.loads((ROOT/'demo-agent.json').read_text())['deployment_id']
    route='/api/v1/agent-deployments/'+deployment+'/runs'
    body={'input':{'message':'仅回复 OK。不要调用工具，不要生成文件。','history':[]},'timeoutSeconds':30}

    def request(path,method='GET',body=None,expected=(200,),authenticated=True,accept='application/json'):
        req=urllib.request.Request(session.api+path,method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={'Content-Type':'application/json','Accept':accept,'Origin':session.api,**(session.auth if authenticated else {})})
        try:response=session.opener.open(req,timeout=30)
        except urllib.error.HTTPError as failure:response=failure
        with response:
            assert response.status in expected,'unexpected_deletion_http_status_'+str(response.status)
            data=json.load(response)
            return response.status,data

    if journal.exists():
        state=json.loads(journal.read_text());assert state['owner']==owner and state['deployment']==deployment
    else:
        state={'owner':owner,'deployment':deployment,'key':'deletion-verification-'+str(uuid4())}
        private_file(journal,json.dumps(state))
    if 'run_id' not in state:
        accepted=session.call(route,body,{'Idempotency-Key':state['key']})
        state['run_id']=accepted['run_id'];private_file(journal,json.dumps(state),replace=True)
    run=state['run_id'];assert str(UUID(run))==run
    path='/api/v1/runs/'+run
    request(path,'DELETE',expected=(401,),authenticated=False)
    existing_status,_=request(path+'/deletion',expected=(200,404))
    if existing_status==200 and not state.get('deleted'):
        state['deleted']=True;private_file(journal,json.dumps(state),replace=True)
    if not state.get('deleted'):
        reused=session.call(route,body,{'Idempotency-Key':state['key']})
        assert reused['run_id']==run and reused['reused']
        assert session.call(path)['deployment_id']==deployment
        state['submission_reuse_verified']=True;private_file(journal,json.dumps(state),replace=True)
        session.call(path+'/cancel',{})
        for _ in range(90):
            status,result=request(path,'DELETE',expected=(202,409))
            if status==202:
                state['deleted']=True;private_file(journal,json.dumps(state),replace=True);break
            assert result['message']=='run_delete_not_ready'
            time.sleep(2)
        else:raise AssertionError('task_deletion_not_ready')
    request(path,'DELETE',expected=(202,))
    for suffix in ('','/events','/observations','/outputs'):
        request(path+suffix,expected=(404,))
    request(path+'/events',expected=(404,),accept='text/event-stream')
    assert all(item['run_id']!=run for item in session.call(route))
    # Reusing the retained request key must fail, never recreate or replay the task.
    req=urllib.request.Request(session.api+route,method='POST',data=json.dumps(body).encode(),
        headers={**session.auth,'Content-Type':'application/json','Idempotency-Key':state['key']})
    try:session.opener.open(req,timeout=30);raise AssertionError('deleted_task_replayed')
    except urllib.error.HTTPError as failure:
        with failure:assert failure.code==409 and json.load(failure)['message']=='run_deleted'
    for _ in range(75):
        receipt=session.call(path+'/deletion')
        if receipt['files_status']=='complete':break
        time.sleep(2)
    else:raise AssertionError('background_deletion_not_complete')
    report={'checked_at':datetime.now(timezone.utc).isoformat(),'run_id':run,'https_verified':True,
        'only_owned_synthetic_task_deleted':True,'unauthenticated_rejected':True,'repeated_delete_idempotent':True,
        'status_events_sse_observations_outputs_hidden':True,'removed_from_list':True,
        'same_idempotency_key_cannot_replay':True,'background_cleanup_status':receipt['files_status'],
        'submission_reuse_verified':state.get('submission_reuse_verified',False),'browser_verification':False}
    private_file(ROOT/'task-deletion-verification.json',json.dumps(report,indent=2)+'\n',replace=True)
    print(json.dumps(report),flush=True)


if __name__=='__main__':main()
