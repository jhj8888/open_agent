import assert from 'node:assert/strict';
import test from 'node:test';
import { workflowBotId, workflowBotNumber } from './workflow-metadata.ts';
import { readModelAuthStatus } from '../space-page/agent-page/components/create-bot/model-auth.ts';

test('workflow bot IDs preserve string precision and ignore malformed metadata', () => {
  assert.equal(
    workflowBotId('{"botId":"9007199254740993"}'),
    '9007199254740993'
  );
  assert.equal(workflowBotId('{"botId":42}'), 42);
  for (const invalid of [undefined, '', '{', 'null', '[]', '{"botId":{}}'])
    assert.equal(workflowBotId(invalid), undefined);
});

test('model authorization uses validated API rows without pretending the response is AxiosResponse', () => {
  assert.deepEqual(readModelAuthStatus({ data: [] }), []);
  assert.deepEqual(
    readModelAuthStatus([
      null,
      { domain: 'model', name: 'Model', status: 'approved' },
    ]),
    []
  );
  const rows = readModelAuthStatus([
    {
      domain: 'model',
      name: 'Model',
      status: 0,
      serviceId: null,
      modelType: 1,
      info: '{}',
    },
  ]);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].status, 0);
  assert.equal(rows[0].serviceId, undefined);
  assert.equal(rows[0].modelType, 1);
  assert.equal(rows[0].info, '{}');
});

test('numeric-only publish props never round a string identifier', () => {
  assert.equal(workflowBotNumber('{"botId":"42"}'), 42);
  assert.equal(workflowBotNumber('{"botId":42}'), 42);
  assert.equal(workflowBotNumber('{"botId":"9007199254740993"}'), undefined);
  assert.equal(workflowBotNumber('{"botId":"not-an-id"}'), undefined);
});
