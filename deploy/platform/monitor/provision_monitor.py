"""Install checksum-locked offline monitoring artifacts into an owned private service."""
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import pwd
import socket
import tarfile
import time
import urllib.request
from monitor_guard import command, main as guard

ROOT=Path('/opt/open-agent/monitor')
OWNER='open_agent.monitor.demo.v1'
HERE=Path(__file__).parent

def write(path,body,mode=0o644,group=None):
    if path.is_symlink(): raise RuntimeError('Linked monitoring destination')
    temporary=path.with_suffix(path.suffix+'.pending')
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,mode)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(body if isinstance(body,bytes) else body.encode());stream.flush();os.fsync(stream.fileno())
        if group is not None: os.chown(temporary,0,group)
        os.chmod(temporary,mode);os.replace(temporary,path)
    finally: temporary.unlink(missing_ok=True)

def install():
    if os.geteuid()!=0 or ROOT.is_symlink(): raise RuntimeError('Monitoring requires owned root installation')
    if ROOT.exists() and (ROOT/'.owner').read_text().strip()!=OWNER: raise RuntimeError('Unowned monitoring directory')
    ROOT.mkdir(mode=0o755,exist_ok=True)
    if not (ROOT/'.owner').exists(): write(ROOT/'.owner',OWNER+'\n',0o600)
    with os.fdopen(os.open(ROOT/'.install.lock',os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW,0o600),'a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        network=json.loads(command('docker','network','inspect','open-agent-control-api').stdout)[0]
        if network.get('Labels',{}).get('io.open-agent.owner')!='open_agent.control.demo.v1': raise RuntimeError('Unowned API network')
        address=network['IPAM']['Config'][0]['Gateway']
        if not ipaddress.ip_address(address).is_private: raise RuntimeError('Monitoring bind address must be private')
        for name,host,port in [('open-agent-prometheus',address,19090),('open-agent-node-exporter','127.0.0.1',19100)]:
            unit=Path('/etc/systemd/system/'+name+'.service')
            if unit.exists() and OWNER not in unit.read_text(): raise RuntimeError('Unowned monitoring service')
            if command('systemctl','is-active','--quiet',name,check=False).returncode:
                with socket.socket() as probe: probe.bind((host,port))
        account='open-agent-monitor'
        try: user=pwd.getpwnam(account)
        except KeyError:
            command('useradd','--system','--user-group','--home-dir',str(ROOT),'--shell','/sbin/nologin',account)
            user=pwd.getpwnam(account)
        if user.pw_dir!=str(ROOT) or user.pw_shell!='/sbin/nologin': raise RuntimeError('Unexpected monitoring account')
        manifest=json.loads((HERE/'artifacts.json').read_text())
        installed={}
        for name,asset in manifest.items():
            path=HERE/asset['archive'];digest=hashlib.sha256(path.read_bytes()).hexdigest()
            if digest!=asset['sha256']: raise RuntimeError('Monitoring artifact checksum mismatch')
            directory=ROOT/(name+'-'+asset['version']);directory.mkdir(mode=0o755,exist_ok=True)
            if directory.is_symlink(): raise RuntimeError('Linked monitoring release')
            names=[name,'LICENSE','NOTICE']+(['promtool'] if name=='prometheus' else [])
            with tarfile.open(path,'r:gz') as archive:
                prefix=asset['archive'].removesuffix('.tar.gz')+'/'
                for filename in names:
                    item=archive.getmember(prefix+filename)
                    if not item.isfile() or item.size>400*1024*1024: raise RuntimeError('Invalid monitoring archive member')
                    data=archive.extractfile(item).read()
                    target=directory/filename
                    if target.is_symlink(): raise RuntimeError('Linked monitoring binary')
                    if target.exists() and target.read_bytes()!=data: raise RuntimeError('Immutable monitoring binary changed')
                    if not target.exists(): write(target,data,0o755 if filename in [name,'promtool'] else 0o644)
            installed[name]=str(directory/name)
        data=ROOT/'data';data.mkdir(mode=0o750,exist_ok=True)
        if data.is_symlink(): raise RuntimeError('Linked monitoring data')
        os.chown(data,user.pw_uid,user.pw_gid)
        secrets=json.loads(Path('/opt/open-agent/control/api-secrets.json').read_text())
        write(ROOT/'observer.key',secrets['observer_key'],0o640,user.pw_gid)
        write(ROOT/'alerts.yml',(HERE/'alerts.yml').read_bytes())
        config=f'''global:
  scrape_interval: 15s
  evaluation_interval: 15s
rule_files: ['{ROOT}/alerts.yml']
scrape_configs:
  - job_name: open-agent-platform
    metrics_path: /internal/platform/metrics
    follow_redirects: false
    http_headers:
      X-Platform-Key-Id:
        values: [demo-observer]
      X-Platform-Service-Key:
        files: ['{ROOT}/observer.key']
    static_configs:
      - targets: ['127.0.0.1:18082']
  - job_name: open-agent-node
    follow_redirects: false
    static_configs:
      - targets: ['127.0.0.1:19100']
'''
        write(ROOT/'prometheus.yml',config,0o640,user.pw_gid)
        promtool=str(Path(installed['prometheus']).with_name('promtool'))
        command(promtool,'check','config',str(ROOT/'prometheus.yml'))
        command(promtool,'check','rules',str(ROOT/'alerts.yml'))
        # Resolve the fixture's relative rule path against its verified source directory.
        previous=Path.cwd()
        try:
            os.chdir(HERE)
            command(promtool,'test','rules','alerts.test.yml')
        finally: os.chdir(previous)
        write(ROOT/'monitor_guard.py',(HERE/'monitor_guard.py').read_bytes())
        guard()
        common='''Restart=on-failure
RestartSec=5
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
CapabilityBoundingSet=
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
LockPersonality=yes
RestrictSUIDSGID=yes
UMask=0077
'''
        units={
            'open-agent-monitor-guard':f'''[Unit]
After=docker.service firewalld.service
Requires=docker.service
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 {ROOT}/monitor_guard.py
RemainAfterExit=yes
[Install]
WantedBy=multi-user.target
''',
            'open-agent-node-exporter':f'''[Unit]
Description=Open Agent private host metrics
After=network-online.target
[Service]
DynamicUser=yes
ExecStart={installed['node_exporter']} --web.listen-address=127.0.0.1:19100 --collector.disable-defaults --collector.cpu --collector.meminfo --collector.filesystem --collector.loadavg --collector.time --collector.systemd --collector.systemd.unit-include=open-agent-(supervisor|temporal|ingress).service
MemoryMax=96M
CPUQuota=10%
{common}[Install]
WantedBy=multi-user.target
''',
            'open-agent-prometheus':f'''[Unit]
Description=Open Agent private metrics and alert rules
Requires=open-agent-monitor-guard.service
After=open-agent-monitor-guard.service open-agent-node-exporter.service
Wants=open-agent-node-exporter.service
[Service]
User={account}
Group={account}
ExecStart={installed['prometheus']} --config.file={ROOT}/prometheus.yml --storage.tsdb.path={data} --storage.tsdb.retention.time=3d --storage.tsdb.retention.size=256MB --web.listen-address={address}:19090 --query.timeout=5s --query.max-concurrency=4
ReadWritePaths={data}
MemoryMax=384M
CPUQuota=30%
{common}[Install]
WantedBy=multi-user.target
'''}
        for name,body in units.items():
            path=Path('/etc/systemd/system/'+name+'.service')
            if path.exists() and OWNER not in path.read_text(): raise RuntimeError('Unowned monitoring unit')
            write(path,'# '+OWNER+'\n'+body)
        command('systemctl','daemon-reload')
        for name in units:
            command('systemctl','enable',name)
            command('systemctl','restart',name)
        endpoint='http://'+address+':19090'
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(30):
            try:
                with opener.open(endpoint+'/-/ready',timeout=2) as response:
                    if response.status==200: break
            except Exception: time.sleep(1)
        else: raise RuntimeError('Owned Prometheus not ready')
        report={'owner':OWNER,'prometheus_url':endpoint,'node_endpoint':'http://127.0.0.1:19100',
                'artifacts':manifest,'retention':'3d / 256MB','public_listener':False,
                'alert_rules_valid':True,'alert_behavior_verified':True}
        write(ROOT/'deployment.json',json.dumps(report,indent=2)+'\n',0o600)
        print(json.dumps(report),flush=True)

if __name__=='__main__': install()
