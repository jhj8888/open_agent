import assert from 'node:assert/strict';
import test from 'node:test';
import { importedToolDraft } from './import-contracts.ts';

test('plugin import keeps configuration without inventing persisted fields', () => {
  const draft = importedToolDraft({
    name: 'Imported',
    endPoint: 'https://example.test',
    authType: 0,
    webSchema: '{"toolRequestInput":[]}',
  });
  assert.equal(draft.name, 'Imported');
  assert.equal(draft.authType, 0);
  assert.equal(draft.webSchema, '{"toolRequestInput":[]}');
  assert.equal(Object.hasOwn(draft, 'id'), false);
  assert.equal(Object.hasOwn(draft, 'status'), false);
});

test('nullable or missing export fields remain optional draft values', () => {
  const draft = importedToolDraft({
    name: null,
    webSchema: null,
    authType: null,
  });
  assert.equal(draft.name, undefined);
  assert.equal(draft.webSchema, undefined);
  assert.equal(draft.authType, undefined);
  assert.equal(draft.avatarColor, undefined);
});
