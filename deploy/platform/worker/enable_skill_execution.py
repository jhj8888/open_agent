"""Enable only the demo gVisor adapter after installing its independent verifier."""

import json
from provision_supervisor import ROOT, OWNER, write, install

if __name__ == '__main__':
    if (ROOT / '.owner').read_text().strip() != OWNER:
        raise RuntimeError('Unowned supervisor installation')
    write(ROOT / 'sandbox-enabled.json', json.dumps({'profile': 'gvisor-demo'}) + '\n')
    install()
