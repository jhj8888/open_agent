"""Enable authenticated execution on the existing loopback demo API."""

import json
from provision_data import ROOT, installation_lock, private_file
from provision_api import main

if __name__ == '__main__':
    with installation_lock():
        private_file(ROOT / 'execution-admission.json',
                     json.dumps({'enabled': True, 'supervisor': 'demo-supervisor'}) + '\n', replace=True)
        main()
