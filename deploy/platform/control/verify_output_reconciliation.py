"""Actual deployed TaskOutputService with disposable MySQL and S3, no public ports."""
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import secrets
import tempfile
import time
import uuid
import zipfile
import sys

sys.path.append(str(Path(__file__).resolve().parents[1] / 'backup'))
import deletion_overlay

from provision_data import ROOT, MYSQL, SEAWEED, command, private_file
from provision_api import JRE, ARTIFACT_SHA256


def main(multipart_only=False):
    os.umask(0o077)
    available=int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:')))
    assert available>=1536*1024,'insufficient_memory_for_disposable_recovery_fixture'
    name='oa-output-reconcile-'+uuid.uuid4().hex[:12]
    label='open-agent.output-reconciliation-fixture'
    folder=Path(tempfile.mkdtemp(prefix=name+'-',dir='/opt/open-agent/verification'))
    folder.chmod(0o755)
    jar=ROOT/'releases'/ARTIFACT_SHA256/'platform.jar'
    assert sha256(jar.read_bytes()).hexdigest()==ARTIFACT_SHA256
    probe=Path(__file__).with_name('output-reconciliation-probe.jar')
    (folder/'probe.jar').write_bytes(probe.read_bytes());(folder/'probe.jar').chmod(0o444)
    with zipfile.ZipFile(jar) as archive:
        for member in archive.namelist():
            if member.startswith('BOOT-INF/lib/') and member.endswith('.jar'):
                target=folder/'lib'/Path(member).name
            elif (member.startswith('BOOT-INF/classes/com/iflytek/astron/console/hub/platform/') and member.endswith('.class')) or (member.startswith('BOOT-INF/classes/db/migration/') and member.endswith('.sql')):
                target=folder/'classes'/member.removeprefix('BOOT-INF/classes/')
            else:continue
            assert target.resolve().is_relative_to(folder.resolve())
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(archive.read(member));target.chmod(0o444)
    for path in folder.rglob('*'):
        if path.is_dir():path.chmod(0o755)
    key,secret='fixture_'+secrets.token_hex(12),secrets.token_hex(32)
    db_password,root_password=secrets.token_hex(32),secrets.token_hex(32)
    private_file(folder/'s3.json',json.dumps({'identities':[{'name':'fixture','credentials':[{'accessKey':key,'secretKey':secret}],
        'actions':['Admin','Read','Write','List']}]}),uid=65532,gid=65532)
    private_file(folder/'mysql.env','MYSQL_ROOT_PASSWORD='+root_password+'\nMYSQL_DATABASE=oa_output_fixture\nMYSQL_USER=fixture_output\nMYSQL_PASSWORD='+db_password+'\n')
    private_file(folder/'java.env','FIXTURE_ENDPOINT=http://127.0.0.1:8333\nFIXTURE_KEY='+key+'\nFIXTURE_SECRET='+secret+
        '\nFIXTURE_JDBC_URL=jdbc:mysql://127.0.0.1:3306/oa_output_fixture?useSSL=false&allowPublicKeyRetrieval=true&connectTimeout=5000&socketTimeout=15000\nFIXTURE_DB_PASSWORD='+db_password+'\n')
    python_probe=Path(__file__).with_name('output_multipart_probe.py')
    (folder/'multipart.py').write_bytes(python_probe.read_bytes());(folder/'multipart.py').chmod(0o444)
    containers=[];volumes=[];phases=[]

    def owned_container(identity):
        data=json.loads(command('docker','inspect',identity).stdout)[0]
        assert data['Id']==identity and data['Config']['Labels'][label]==name
        return data

    def create_volume(suffix,uid,target):
        volume=name+'-'+suffix
        command('docker','volume','create','--label',label+'='+name,volume);volumes.append(volume)
        mount='type=volume,source='+volume+',target='+target+',volume-nocopy'
        command('docker','run','--pull','never','--rm','--network','none','--read-only','--user','0:0',
            '--cap-drop','ALL','--cap-add','CHOWN','--memory','64m','--pids-limit','16',
            '--mount',mount,'--entrypoint','chown',JRE,str(uid)+':'+str(uid),target)
        return mount

    def java_args(mode,network):
        return ['--network',network,'--read-only','--user','10001:10001','--cap-drop','ALL',
            '--security-opt','no-new-privileges','--memory','256m','--memory-swap','256m','--cpus','1',
            '--pids-limit','96','--tmpfs','/tmp:rw,nosuid,nodev,size=64m,mode=1777','--env-file',str(folder/'java.env'),
            '--mount','type=bind,source='+str(folder)+',target=/fixture,readonly','--entrypoint','java',JRE,
            '-Xmx128m','-cp','/fixture/probe.jar:/fixture/classes:/fixture/lib/*','OutputReconciliationProbe',mode]

    def run_probe(mode,network):
        client_name=name+'-'+mode
        try:
            result=command('docker','run','--pull','never','--rm','--name',client_name,'--label',label+'='+name,
                *java_args(mode,network),timeout=180,check=False)
        finally:
            found=command('docker','inspect',client_name,check=False)
            if found.returncode==0:
                identity=json.loads(found.stdout)[0]['Id'];owned_container(identity)
                command('docker','rm','-f',identity)
        private_file(folder/(mode+'.log'),result.stdout+result.stderr)
        assert result.returncode==0 and 'OUTPUT_RECONCILIATION_OK='+mode in result.stdout,'reconciliation_probe_failed_'+mode
        phases.append(mode)

    def service(suffix,image,options,args):
        identity=command('docker','run','--pull','never','-d','--name',name+'-'+suffix,'--label',label+'='+name,
            '--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--pids-limit','128',
            *options,image,*args).stdout.strip()
        containers.append(identity);return identity

    try:
        for image in (JRE,MYSQL,SEAWEED):command('docker','image','inspect',image)
        mysql_mount=create_volume('mysql',999,'/var/lib/mysql')
        s3_mount=create_volume('s3',65532,'/data')
        mysql=service('mysql',MYSQL,['--network','none','--user','999:999','--memory','512m','--memory-swap','512m',
            '--cpus','1','--tmpfs','/tmp:rw,nosuid,noexec,size=64m','--tmpfs','/var/run/mysqld:rw,nosuid,size=8m,uid=999,gid=999',
            '--mount',mysql_mount,'--env-file',str(folder/'mysql.env')],
            ['--innodb-buffer-pool-size=64M','--max-connections=20','--performance-schema=OFF',
             '--mysqlx=0','--skip-name-resolve','--log-error-verbosity=1','--bind-address=127.0.0.1'])
        for _ in range(90):
            ready=command('docker','exec',mysql,'sh','-c',
                'MYSQL_PWD="$MYSQL_PASSWORD" mysql -h127.0.0.1 -ufixture_output oa_output_fixture -Nse "SELECT 1"',check=False)
            if ready.returncode==0 and ready.stdout.strip()=='1':break
            assert owned_container(mysql)['State']['Running'],'mysql_fixture_exited'
            time.sleep(1)
        else:raise AssertionError('mysql_fixture_startup_timeout')
        namespace='container:'+mysql
        s3=service('s3',SEAWEED,['--network',namespace,'--user','65532:65532','--memory','384m','--memory-swap','384m',
            '--cpus','1','--tmpfs','/tmp:rw,nosuid,nodev,size=32m','-e','GOMEMLIMIT=256MiB','--mount',s3_mount,
            '--mount','type=bind,source='+str(folder/'s3.json')+',target=/s3.json,readonly','--entrypoint','weed'],
            ['server','-dir=/data','-ip=127.0.0.1','-ip.bind=127.0.0.1','-s3','-s3.ip.bind=127.0.0.1','-s3.config=/s3.json',
             '-master.telemetry=false','-master.volumeSizeLimitMB=64','-volume.max=4','-volume.minFreeSpace=0','-volume.index=leveldb',
             '-s3.autoCreateBucket=false','-s3.iam=false','-s3.port.iceberg=0','-s3.port.lance=0',
             '-s3.concurrentUploadLimitMB=16','-s3.concurrentFileUploadLimit=4','-s3.readerCacheSizeMB=16'])
        time.sleep(8)
        run_probe('prepare',namespace)
        if not multipart_only:
            crash=command('docker','run','--pull','never','-d','--name',name+'-crash','--label',label+'='+name,
                *java_args('crash-upload',namespace)).stdout.strip();containers.append(crash)
            for _ in range(60):
                logs=command('docker','logs',crash)
                if 'OUTPUT_RECONCILIATION_READY=crash-upload' in logs.stdout:break
                assert owned_container(crash)['State']['Running'],'crash_fixture_exited_before_checkpoint'
                time.sleep(1)
            else:raise AssertionError('crash_fixture_checkpoint_timeout')
            run_probe('candidate-present',namespace)
            command('docker','kill','--signal=KILL',crash)
            assert command('docker','wait',crash).stdout.strip()=='137'
            phases.append('uploader-sigkill')
            command('docker','kill','--signal=KILL',s3)
            assert command('docker','wait',s3).stdout.strip()=='137'
            run_probe('outage',namespace)
            command('docker','start',s3);time.sleep(8)
            run_probe('recover',namespace)
            late=command('docker','run','--pull','never','-d','--name',name+'-late','--label',label+'='+name,
                *java_args('late-upload',namespace)).stdout.strip();containers.append(late)
            for checkpoint in ('late-start','late-committed'):
                for _ in range(60):
                    logs=command('docker','logs',late)
                    if 'OUTPUT_RECONCILIATION_READY='+checkpoint in logs.stdout:break
                    assert owned_container(late)['State']['Running'],'late_upload_exited_before_checkpoint'
                    time.sleep(1)
                else:raise AssertionError('late_upload_checkpoint_timeout')
                if checkpoint=='late-start':
                    run_probe('late-reap',namespace)
                    (folder/'late-release').write_text('release');(folder/'late-release').chmod(0o444)
            run_probe('late-present',namespace)
            command('docker','kill','--signal=KILL',late)
            assert command('docker','wait',late).stdout.strip()=='137'
            phases.append('late-uploader-sigkill')
            run_probe('late-recover',namespace)
        multipart=command('docker','run','--pull','never','-d','--name',name+'-multipart','--label',label+'='+name,
            *java_args('multipart-upload',namespace)).stdout.strip();containers.append(multipart)
        for _ in range(60):
            logs=command('docker','logs',multipart)
            if 'OUTPUT_RECONCILIATION_READY=multipart-part' in logs.stdout:break
            assert owned_container(multipart)['State']['Running'],'multipart_exited_before_checkpoint'
            time.sleep(1)
        else:raise AssertionError('multipart_checkpoint_timeout')
        run_probe('multipart-key',namespace)
        key=next(line.split('=',1)[1] for line in (folder/'multipart-key.log').read_text().splitlines() if line.startswith('OUTPUT_MULTIPART_KEY='))
        worker='sha256:c87a7579793fa109b525ed2a6c2f3d1d1b95e43c142b33bd8f84fd59b4c58410'
        def inspect_multipart(mode):
            client_name=name+'-inspect-'+mode
            try:
                result=command('docker','run','--rm','--pull','never','--name',client_name,'--label',label+'='+name,
                    '--network',namespace,'--read-only','--user','10001:10001',
                    '--cap-drop','ALL','--security-opt','no-new-privileges','--memory','192m','--pids-limit','64',
                    '--env-file',str(folder/'java.env'),'-e','FIXTURE_OBJECT_KEY='+key,
                    '--mount','type=bind,source='+str(folder)+',target=/fixture,readonly','--entrypoint','python',worker,
                    '/fixture/multipart.py',mode,check=False,timeout=60)
            finally:
                found=command('docker','inspect',client_name,check=False)
                if found.returncode==0:
                    identity=json.loads(found.stdout)[0]['Id'];owned_container(identity)
                    command('docker','rm','-f',identity)
            private_file(folder/('multipart-'+mode+'.log'),result.stdout+result.stderr)
            assert result.returncode==0 and 'OUTPUT_MULTIPART_OK='+mode in result.stdout,'multipart_inspection_failed_'+mode
            phases.append('multipart-'+mode)
        inspect_multipart('present')
        command('docker','kill','--signal=KILL',multipart)
        assert command('docker','wait',multipart).stdout.strip()=='137'
        phases.append('multipart-uploader-sigkill')
        run_probe('multipart-reap',namespace)
        inspect_multipart('absent')
        if not multipart_only:
            run_probe('delete-seed',namespace)
            snapshot=command('docker','exec',mysql,'sh','-c',
                'MYSQL_PWD="$MYSQL_PASSWORD" mysqldump -h127.0.0.1 -ufixture_output --no-tablespaces --set-gtid-purged=OFF oa_output_fixture').stdout
            run_probe('delete-prepare',namespace)
            run_probe('delete-after-snapshot',namespace)
            def sql(statement):
                owned_container(mysql)
                result=command('docker','exec','-i',mysql,'sh','-c',
                    'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql --batch --raw --skip-reconnect -uroot -N oa_output_fixture',
                    input=statement,check=False)
                if result.returncode:
                    private_file(folder/'overlay-sql-error.log',result.stderr,replace=True)
                    raise RuntimeError('deletion_overlay_fixture_sql_failed')
                return result.stdout.strip()
            records=deletion_overlay.export(sql)
            assert len(records)==2
            sql(snapshot);del snapshot
            assert sql("SELECT state FROM oa_run_output WHERE file_name='deleted.txt'")=='available'
            conflict=[dict(row) for row in records]
            conflict[0]['tenant_id']='wrong-scope'
            # Choose the existing task so the scope mismatch must be detected.
            existing=next(row for row in conflict if row['idempotency_key']=='delete-fixture')
            existing['tenant_id']='wrong-scope'
            try:deletion_overlay.merge(sql,conflict)
            except RuntimeError:pass
            else:raise AssertionError('conflicting_overlay_accepted')
            assert sql('SELECT COUNT(*) FROM oa_run_deletion_fence')=='0'
            sql("CREATE TRIGGER fail_restore_audit BEFORE INSERT ON oa_tenant_audit FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='synthetic restore failure'")
            try:deletion_overlay.merge(sql,records)
            except RuntimeError:pass
            else:raise AssertionError('failed_audit_did_not_abort_overlay')
            sql('DROP TRIGGER fail_restore_audit')
            assert sql('SELECT COUNT(*) FROM oa_run_deletion_fence')=='0'
            assert sql("SELECT state FROM oa_run_output WHERE file_name='deleted.txt'")=='available'
            overlay=deletion_overlay.merge(sql,records)
            assert overlay['restored_tasks_hidden']==1 and overlay['absent_tasks_fenced']==1
            assert deletion_overlay.merge(sql,records)==overlay
            assert deletion_overlay.export(sql)==records
            phases.append('deletion-overlay-rollback-idempotency-roundtrip')
            run_probe('restore-deletions-check',namespace)
            command('docker','kill','--signal=KILL',s3)
            assert command('docker','wait',s3).stdout.strip()=='137'
            run_probe('delete-outage',namespace)
            command('docker','start',s3);time.sleep(8)
            run_probe('delete-recover',namespace)
    finally:
        # Inspect exact identities and labels before any destructive operation.
        for identity in reversed(containers):
            data=owned_container(identity)
            logs=command('docker','logs',identity,check=False)
            private_file(folder/(identity[:12]+'.log'),logs.stdout+logs.stderr)
            private_file(folder/(identity[:12]+'.state.json'),json.dumps({'exit_code':data['State']['ExitCode'],'oom_killed':data['State']['OOMKilled']}))
            command('docker','rm','-f',identity)
        for volume in volumes:
            data=json.loads(command('docker','volume','inspect',volume).stdout)[0]
            assert data['Labels'][label]==name
            command('docker','volume','rm',volume)
        for filename in ['s3.json','java.env','mysql.env']:
            path=folder/filename;assert path.parent==folder and not path.is_symlink();path.unlink(missing_ok=True)
    result={'checked_at':datetime.now(timezone.utc).isoformat(),'api_artifact_sha256':ARTIFACT_SHA256,
        'probe_sha256':sha256(probe.read_bytes()).hexdigest(),'mysql_image':MYSQL,'s3_image':SEAWEED,'phases':phases,
        'actual_task_output_service_and_run_ledger':True,'migrations':'upstream dependency tables + V1.49 through V1.69 from verified release JAR',
        'uploader_sigkill_after_s3_commit_before_publication':True,'writing_record_persisted':True,
        'offline_reconciliation_preserves_reaping_for_retry':True,'failed_upload_has_no_public_object_reference':True,
        'initial_recovery_cleared_uploads':3,'final_recovery_cleared_uploads':4,'retained_published_uploads':2,'retry_uses_different_immutable_key':True,
        'recovery_counts_exclude_multipart_phase':True,
        'repeated_reconciliation_keeps_published_objects':True,'network':'none; S3/Java share disposable MySQL network namespace',
        'object_committed_after_first_reap_is_eventually_removed':True,
        'multipart_interruption':{'uploaded_part_bytes':5242880,'production_reconciliation_removes_incomplete_upload':True,
            'independent_boto3_verification':True,'production_multipart_cleanup_implemented':True},
        'fixture_containers_volumes_credentials_removed':True,'production_data_modified':False,
        'http_authorization_verified':False,'aging':'fixture created_at/reconcile_after advanced explicitly; no 15-minute/24-hour wall-clock wait'}
    if multipart_only:
        result={key:result[key] for key in ['checked_at','api_artifact_sha256','probe_sha256','mysql_image','s3_image','phases','multipart_interruption','fixture_containers_volumes_credentials_removed','production_data_modified','network']}
    else:
        result['task_deletion']={'real_authorization_and_service':True,'idempotent_audit':True,'read_hidden':True,
            'storage_outage_pending':True,'recovery_clears_exact_object':True,'retained_file_digest_matches':True}
        result['deletion_overlay']={**overlay,'snapshot_restored':True,'audit_failure_rolls_back':True,
            'scope_conflict_rejected':True,'repeated_merge_idempotent':True,'reexport_preserves_absent_tasks':True,
            'actual_java_refuses_both_original_request_keys':True}
    private_file(ROOT/'output-multipart-verification.json',json.dumps(result,indent=2)+'\n',replace=True)
    print(json.dumps(result),flush=True)


if __name__=='__main__':main()
