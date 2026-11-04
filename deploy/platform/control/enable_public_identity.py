"""Move the demo issuer to its verified HTTPS origin while retaining signing keys."""

import json
import urllib.request
from provision_data import ROOT, container_address, installation_lock, private_file


def keys():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open('http://' + container_address('open-agent-control-identity') + ':8000/.well-known/jwks', timeout=10) as response:
        return json.load(response)


if __name__ == '__main__':
    with installation_lock():
        before = keys()
        private_file(ROOT / 'public-origin.json', json.dumps({'origin': 'https://120.26.81.83/identity'}) + '\n', replace=True)
        from provision_identity import main
        main()
        if keys() != before:
            raise RuntimeError('Identity signing keys changed unexpectedly')
        print(json.dumps({'signing_keys_preserved': True}), flush=True)
