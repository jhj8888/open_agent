"""Crash only a disposable S3 server; exercise the actual deployed Java store."""
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

from provision_data import ROOT, SEAWEED, command, private_file
from provision_api import JRE, ARTIFACT_SHA256


def main():
    os.umask(0o077)
    name = 'oa-output-store-fixture-' + uuid.uuid4().hex[:12]
    label = 'open-agent.output-store-fixture'
    folder = Path(tempfile.mkdtemp(prefix=name+'-', dir='/opt/open-agent/verification'))
    folder.chmod(0o755)
    jar = ROOT/'releases'/ARTIFACT_SHA256/'platform.jar'
    assert sha256(jar.read_bytes()).hexdigest() == ARTIFACT_SHA256
    source = Path(__file__).with_name('output-store-probe.jar')
    (folder/'probe.jar').write_bytes(source.read_bytes()); (folder/'probe.jar').chmod(0o444)
    with zipfile.ZipFile(jar) as archive:
        for member in archive.namelist():
            if member.startswith('BOOT-INF/lib/') and member.endswith('.jar'):
                target = folder/'lib'/Path(member).name
            elif member.startswith('BOOT-INF/classes/com/iflytek/astron/console/hub/platform/run/') and any('/'+c in member for c in ['MinioTaskOutputStore','TaskOutputStore','RunFailure']):
                target = folder/'classes'/member.removeprefix('BOOT-INF/classes/')
            else: continue
            assert target.resolve().is_relative_to(folder.resolve())
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(archive.read(member)); target.chmod(0o444)
    for path in folder.rglob('*'):
        if path.is_dir(): path.chmod(0o755)
    key, secret = 'fixture_'+secrets.token_hex(12), secrets.token_hex(32)
    private_file(folder/'s3.json',json.dumps({'identities':[{'name':'fixture','credentials':[{'accessKey':key,'secretKey':secret}],
        'actions':['Admin','Read','Write','List']}]}),uid=65532,gid=65532)
    private_file(folder/'java.env','FIXTURE_ENDPOINT=http://127.0.0.1:8333\nFIXTURE_KEY='+key+'\nFIXTURE_SECRET='+secret+'\n')
    container = volume = crash_client = None
    results = []
    def java_args(mode, network):
        return ['--network',network,'--read-only','--user','10001:10001',
            '--cap-drop','ALL','--security-opt','no-new-privileges','--memory','256m','--memory-swap','256m','--cpus','1',
            '--pids-limit','96','--tmpfs','/tmp:rw,nosuid,nodev,size=64m,mode=1777','--env-file',str(folder/'java.env'),
            '--mount','type=bind,source='+str(folder)+',target=/fixture,readonly','--entrypoint','java',JRE,
            '-Xmx128m','-cp','/fixture/probe.jar:/fixture/classes:/fixture/lib/*','OutputStoreRecoveryProbe',mode]
    def probe(mode, network):
        client_name=name+'-'+mode
        try:
            result = command('docker','run','--pull','never','--rm','--name',client_name,'--label',label+'='+name,
                *java_args(mode,network),timeout=180,check=False)
        finally:
            leftover=command('docker','inspect',client_name,check=False)
            if leftover.returncode==0:
                actual=json.loads(leftover.stdout)[0]
                assert actual['Config']['Labels'][label]==name
                command('docker','rm','-f',actual['Id'])
        private_file(folder/(mode+'.log'),result.stdout+result.stderr,replace=True)
        assert result.returncode == 0 and 'OUTPUT_STORE_PROBE_OK='+mode in result.stdout, 'output_store_probe_failed_'+mode
        results.append(mode)
    try:
        command('docker','image','inspect',SEAWEED); command('docker','image','inspect',JRE)
        command('docker','volume','create','--label',label+'='+name,name); volume=name
        command('docker','run','--pull','never','--rm','--network','none','--read-only','--user','0:0','--cap-drop','ALL',
            '--cap-add','CHOWN','--memory','64m','--pids-limit','16','--mount','type=volume,source='+name+',target=/data,volume-nocopy',
            '--entrypoint','chown',JRE,'65532:65532','/data')
        check=command('docker','run','--pull','never','--rm','--network','none','--read-only','--user','65532:65532','--cap-drop','ALL',
            '--memory','64m','--pids-limit','16','--mount','type=volume,source='+name+',target=/data,volume-nocopy',
            '--entrypoint','sh',JRE,'-c','id; ls -ldn /data; mkdir /data/fixture-write-check && rmdir /data/fixture-write-check',check=False)
        private_file(folder/'volume-permissions.log',check.stdout+check.stderr)
        assert check.returncode==0,'fixture_volume_not_writable'
        container=command('docker','run','--pull','never','-d','--name',name,'--label',label+'='+name,'--network','none',
            '--user','65532:65532','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--memory','384m','--memory-swap','384m',
            '--cpus','1','--pids-limit','128','--tmpfs','/tmp:rw,nosuid,nodev,size=32m','-e','GOMEMLIMIT=256MiB',
            # Copy-up of an empty volume would reset its owner to image UID 1000.
            '--mount','type=volume,source='+name+',target=/data,volume-nocopy',
            '--mount','type=bind,source='+str(folder/'s3.json')+',target=/s3.json,readonly',
            '--entrypoint','weed',SEAWEED,'server','-dir=/data','-ip=127.0.0.1','-ip.bind=127.0.0.1',
            '-s3','-s3.ip.bind=127.0.0.1','-s3.config=/s3.json','-master.telemetry=false',
            '-master.volumeSizeLimitMB=64','-volume.max=4','-volume.minFreeSpace=0','-volume.index=leveldb',
            '-s3.autoCreateBucket=false','-s3.iam=false','-s3.port.iceberg=0','-s3.port.lance=0',
            '-s3.concurrentUploadLimitMB=16','-s3.concurrentFileUploadLimit=4','-s3.readerCacheSizeMB=16').stdout.strip()
        # Bounded startup delay; no business operations are retried.
        time.sleep(8)
        probe('prepare','container:'+container)
        command('docker','kill','--signal=KILL',container)
        probe('outage','none')
        command('docker','start',container)
        time.sleep(8)
        crash_client=command('docker','run','--pull','never','-d','--name',name+'-client','--label',label+'='+name,
            *java_args('post-write-crash','container:'+container)).stdout.strip()
        for _ in range(60):
            logs=command('docker','logs',crash_client)
            if 'OUTPUT_STORE_PROBE_READY=post-write-crash' in logs.stdout: break
            state=json.loads(command('docker','inspect',crash_client).stdout)[0]
            assert state['State']['Running'],'crash_probe_exited_before_checkpoint'
            time.sleep(1)
        else: raise AssertionError('crash_probe_checkpoint_timeout')
        # Independent process verifies S3 really committed the bytes while the
        # uploader is blocked before its final integrity check can return.
        probe('candidate-present','container:'+container)
        command('docker','kill','--signal=KILL',crash_client)
        assert command('docker','wait',crash_client).stdout.strip()=='137'
        results.append('post-write-client-sigkill')
        probe('recover','container:'+container)
    finally:
        if crash_client:
            actual=json.loads(command('docker','inspect',crash_client).stdout)[0]
            assert actual['Id']==crash_client and actual['Config']['Labels'][label]==name
            logs=command('docker','logs',crash_client,check=False)
            private_file(folder/'crash-client.log',logs.stdout+logs.stderr,replace=True)
            command('docker','rm','-f',crash_client)
        if container:
            actual=json.loads(command('docker','inspect',container).stdout)[0]
            assert actual['Id']==container and actual['Config']['Labels'][label]==name
            logs=command('docker','logs',container,check=False)
            private_file(folder/'s3-private.log',logs.stdout+logs.stderr,replace=True)
            private_file(folder/'container-state.json',json.dumps({'exit_code':actual['State']['ExitCode'],'oom_killed':actual['State']['OOMKilled']}),replace=True)
            command('docker','rm','-f',container)
        if volume:
            actual=json.loads(command('docker','volume','inspect',volume).stdout)[0]
            assert actual['Labels'][label]==name
            command('docker','volume','rm',volume)
        for filename in ['s3.json','java.env']:
            path=folder/filename
            assert path.parent==folder and not path.is_symlink()
            path.unlink(missing_ok=True)
    evidence={'checked_at':datetime.now(timezone.utc).isoformat(),'api_artifact_sha256':ARTIFACT_SHA256,
        'probe_sha256':sha256(source.read_bytes()).hexdigest(),'s3_image':SEAWEED,'phases':results,
        'server_sigkill_and_restart':True,'retained_content_verified':True,'failure_codes_and_retry_verified':True,
        'uploader_sigkill_after_object_commit_before_final_validation':True,
        'independent_process_verified_candidate_before_sigkill':True,
        'exact_candidate_cleanup_idempotent':True,'network':'none; Java shares fixture namespace when S3 is running',
        'fixture_container_volume_credentials_removed':True,'production_data_modified':False,
        'business_database_reconciliation_or_midstream_process_kill_verified':False}
    private_file(ROOT/'output-store-recovery-verification.json',json.dumps(evidence,indent=2)+'\n',replace=True)
    print(json.dumps(evidence),flush=True)


if __name__=='__main__': main()
