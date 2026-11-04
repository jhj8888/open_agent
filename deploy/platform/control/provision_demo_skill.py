"""Publish a real immutable Skill ZIP and bind a test Agent through public contracts."""

import io
import json
from pathlib import Path
import zipfile

import httpx
from demo_session import DemoSession
from provision_data import ROOT, private_file


def main():
    session = DemoSession()
    marker = ROOT / 'demo-skill-agent.json'
    if marker.exists():
        saved = json.loads(marker.read_text())
        session.call('/api/v1/agent-deployments/' + saved['deployment_id'])
        print(json.dumps(saved), flush=True)
        return
    image = json.loads(Path('/opt/open-agent/sandbox/deployment.json').read_text())['runtime_image']
    model = json.loads((ROOT / 'demo-agent.json').read_text())
    memory = io.BytesIO()
    with zipfile.ZipFile(memory, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('SKILL.md', '---\nname: isolation-check\ndescription: 验证隔离环境并计算一份销售汇总\n---\n'
                         '# 隔离验证\n请实际执行 `python3 /skills/isolation-check/scripts/check.py`，报告其 uid、销售总额和检查码。不要猜测结果。\n')
        archive.writestr('scripts/check.py', 'import json, os\n'
                         'values = [1250, 2380, 1790]\n'
                         'assert os.getuid() == 1000\n'
                         'print(json.dumps({"uid":os.getuid(),"total":sum(values),"check":"SKILL-READY-5420"}))\n')
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=45) as client:
        response = client.post(session.api + session.management + '/skill-releases', headers=session.auth,
                               data={'version': 'demo.1', 'runtimeImage': image},
                               files={'archive': ('isolation-check.zip', memory.getvalue(), 'application/zip')})
    if response.status_code != 200 or response.json().get('code') != 0:
        raise RuntimeError('Skill publication failed status=' + str(response.status_code))
    release = response.json()['data']
    deployment = session.call(session.management + '/agent-deployments', {
        'displayName': 'Skill 隔离执行助手', 'agentVersion': 'demo.1',
        'modelConnectionId': model['model_connection_id'], 'modelVersion': model['model_version'],
        'skillReleaseIds': [release['releaseId']],
        'execution': {'instructions': '你是企业自动化助手。用户请求验证环境时，必须按 isolation-check Skill 指引实际执行 Python 脚本，报告真实输出。',
                      'answerInstructions': '', 'maxLoops': 6, 'temperature': 0.2, 'maxTokens': 1024},
    })
    result = {'tenant_id': session.tenant, 'environment': 'test', 'deployment_id': deployment['deployment_id'],
              'model_connection_id': model['model_connection_id'], 'model_version': model['model_version'],
              'skill_release_id': release['releaseId']}
    private_file(marker, json.dumps(result, indent=2) + '\n')
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'error_type': type(error).__name__, 'message': str(error) if isinstance(error, RuntimeError) else 'Skill provisioning failed'}), flush=True)
        raise SystemExit(1) from None
