import test from 'node:test';
import { URL } from 'node:url';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

const source = readFileSync(
  new URL('./prompt-model.ts', import.meta.url),
  'utf8'
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    target: ts.ScriptTarget.ES2020,
    module: ts.ModuleKind.CommonJS,
  },
}).outputText;
const exports = {};
new Function('exports', compiled)(exports);
const { parsePromptModel } = exports;

test('malformed and text-only templates do not supply a replacement model', () => {
  for (const value of [
    '',
    '{',
    'null',
    '[]',
    'false',
    '42',
    '{}',
    '{"name":"Prompt only"}',
  ]) {
    assert.equal(parsePromptModel(value), undefined);
  }
});

test('model IDs, disabled thinking and fine-tuned source survive JSON decoding', () => {
  assert.deepEqual(
    parsePromptModel(
      JSON.stringify({
        id: 0,
        llmId: 24,
        domain: 'model-name',
        serviceId: 'service',
        isThink: false,
        llmSource: 2,
        provider: 'openai',
        name: 'Model',
      })
    ),
    {
      id: 0,
      llmId: 24,
      domain: 'model-name',
      serviceId: 'service',
      isThink: false,
      llmSource: 2,
      provider: 'openai',
      name: 'Model',
    }
  );
});

test('invalid optional fields and unrelated metadata cannot replace typed model values', () => {
  assert.deepEqual(
    parsePromptModel(
      JSON.stringify({
        id: 7,
        llmId: 'wrong',
        isThink: 'false',
        llmSource: 99,
        provider: {},
        url: ['wrong'],
        unexpected: 'ignored',
      })
    ),
    { id: 7 }
  );
});
