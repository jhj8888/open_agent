"""Only the owned API network and host may initiate connections to monitor port 19090."""
import ipaddress
import json
import subprocess

def command(*args, check=True):
    result=subprocess.run(args,capture_output=True,text=True,timeout=30)
    if check and result.returncode: raise RuntimeError('Monitor guard operation failed: '+args[0])
    return result

def main():
    network=json.loads(command('docker','network','inspect','open-agent-control-api').stdout)[0]
    if network.get('Labels',{}).get('io.open-agent.owner')!='open_agent.control.demo.v1':
        raise RuntimeError('Unowned API network')
    subnet=ipaddress.ip_network(network['IPAM']['Config'][0]['Subnet'])
    if subnet.version!=4 or not subnet.is_private or subnet.prefixlen<16:
        raise RuntimeError('Unexpected API network range')
    rule=['-p','tcp','--dport','19090','!','-s',str(subnet),'!','-i','lo',
          '-m','conntrack','!','--ctstate','ESTABLISHED,RELATED','-m','comment','--comment','open-agent-monitor-v1','-j','DROP']
    if command('systemctl','is-active','--quiet','firewalld',check=False).returncode==0:
        for scope in [[],['--permanent']]:
            args=['firewall-cmd',*scope,'--direct']
            if command(*args,'--query-rule','ipv4','filter','INPUT','-120',*rule,check=False).returncode:
                command(*args,'--add-rule','ipv4','filter','INPUT','-120',*rule)
    if command('iptables','-C','INPUT',*rule,check=False).returncode:
        command('iptables','-I','INPUT','1',*rule)

if __name__=='__main__': main()
