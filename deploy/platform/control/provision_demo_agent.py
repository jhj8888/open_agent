"""Register the authorized demo model via the real authenticated/encrypted API."""

import json
import sys
from demo_session import DemoSession
from provision_data import ROOT, private_file


def main():
    configuration = json.loads(sys.stdin.read(65537))
    session = DemoSession()
    path = ROOT / 'demo-agent.json'
    if path.exists():
        saved = json.loads(path.read_text())
        deployment = session.call('/api/v1/agent-deployments/' + saved['deployment_id'])
        if deployment['state'] != 'test' or deployment['tenant_id'] != session.tenant:
            raise RuntimeError('Demo deployment no longer available')
        print(json.dumps(saved), flush=True)
        return
    models = session.call(session.management + '/models')
    owned = [model for model in models if model['display_name'] == 'DeepSeek 演示接口']
    if len(owned) > 1:
        raise RuntimeError('Ambiguous demo model connection')
    if owned:
        model = {'connectionId': owned[0]['connection_id'], 'version': owned[0]['current_version']}
    else:
        model = session.call(session.management + '/models', {
            'displayName': 'DeepSeek 演示接口', 'endpoint': configuration['base_url'], 'modelName': configuration['model'],
            'supportsStream': True, 'supportsTools': True, 'supportsUsage': True,
            'connectTimeoutSeconds': 10, 'readTimeoutSeconds': 90, 'totalTimeoutSeconds': 180,
            'apiKey': configuration['api_key'], 'expectedVersion': 0, 'revokePrevious': False,
        })
    deployment = session.call(session.management + '/agent-deployments', {
        'displayName': '企业工作助手', 'agentVersion': 'demo.1',
        'modelConnectionId': model['connectionId'], 'modelVersion': model['version'], 'skillReleaseIds': [],
        'execution': {'instructions': '你是企业工作助手。使用中文清晰、简洁地回答用户。请直接回答，不要调用文件、网络或委派工具。',
                      'answerInstructions': '', 'maxLoops': 4, 'temperature': 0.2, 'maxTokens': 512},
    })
    result = {'tenant_id': session.tenant, 'environment': 'test', 'deployment_id': deployment['deployment_id'],
              'model_connection_id': model['connectionId'], 'model_version': model['version']}
    private_file(path, json.dumps(result, indent=2) + '\n')
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'error_type': type(error).__name__, 'message': str(error) if isinstance(error, RuntimeError) else 'Demo registration failed'}), flush=True)
        raise SystemExit(1) from None
