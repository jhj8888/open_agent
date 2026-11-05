"""Run the owned real-controller fixture after installing the matching SDK overlay.

Flat inputs: current cluster-contract-manifest.json, upstream CRDs/role and
the generated Kata profile JSON. The pinned controller image must be prepared.
"""
import asyncio
import json
from pathlib import Path
import traceback

import provision_api_fixture
import cleanup_api_fixture
import verify_sandbox_resources  # Select the verified overlay before importing SDK consumers.
from controller_fixture import ControllerFixture


def main():
    root = Path('/opt/open-agent/k3s-api-fixture')
    controller = ControllerFixture()
    provision_api_fixture.main()
    try:
        asyncio.run(verify_sandbox_resources.verify(controller))
    except Exception as error:
        (root / 'controller-error.txt').write_text(traceback.format_exc())
        print(json.dumps({'event': 'controller_api_failed', 'type': type(error).__name__}), flush=True)
        raise SystemExit(1) from None
    finally:
        controller.stop()
        cleanup_api_fixture.main()


if __name__ == '__main__':
    main()
