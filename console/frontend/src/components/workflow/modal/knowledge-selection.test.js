import test from 'node:test';
import { URL } from 'node:url';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

const source = readFileSync(
  new URL('./knowledge-selection.ts', import.meta.url),
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
const { toggleKnowledgeSelection } = exports;
const makeNode = nodeParam => ({
  id: 'knowledge::one',
  type: 'custom',
  nodeType: 'knowledge-base',
  position: { x: 0, y: 0 },
  data: {
    nodeMeta: { aliasName: 'Knowledge' },
    nodeParam,
    inputs: [],
    outputs: [],
  },
});
const repository = {
  id: 'database-id',
  name: 'Handbook',
  coreRepoId: 'engine-id',
  tag: 'CBG-RAG',
};

test('first selection initializes lists without modifying the source node', () => {
  const original = makeNode({});
  const result = toggleKnowledgeSelection(original, repository, true);
  assert.deepEqual(result.data.nodeParam.repoIds, ['engine-id']);
  assert.deepEqual(result.data.nodeParam.repoList, [repository]);
  assert.equal(result.data.nodeParam.repoType, 2);
  assert.deepEqual(original.data.nodeParam, {});
});

test('removing a repository removes the matching engine ID and keeps other entries', () => {
  const other = { ...repository, id: 'other', coreRepoId: 'other-engine' };
  const original = makeNode({
    repoList: [repository, other],
    repoIds: ['engine-id', 'other-engine'],
  });
  const result = toggleKnowledgeSelection(original, repository, true);
  assert.deepEqual(result.data.nodeParam.repoList, [other]);
  assert.deepEqual(result.data.nodeParam.repoIds, ['other-engine']);
  assert.deepEqual(original.data.nodeParam.repoIds, [
    'engine-id',
    'other-engine',
  ]);
});

test('legacy scalar repo IDs are retained when another repository is selected', () => {
  const other = { ...repository, id: 'other', coreRepoId: 'other-engine' };
  const result = toggleKnowledgeSelection(
    makeNode({ repoList: [repository], repoId: 'engine-id' }),
    other,
    false
  );
  assert.deepEqual(result.data.nodeParam.repoId, ['engine-id', 'other-engine']);
  assert.equal(result.data.nodeParam.ragType, 'CBG-RAG');
});

test('outer repository IDs are supported and incomplete selections are rejected', () => {
  const original = makeNode({});
  const external = {
    id: 'external',
    name: 'External',
    outerRepoId: 'outer-id',
    tag: 'Ragflow-RAG',
  };
  assert.deepEqual(
    toggleKnowledgeSelection(original, external, false).data.nodeParam.repoId,
    ['outer-id']
  );
  assert.equal(
    toggleKnowledgeSelection(
      original,
      { id: 'incomplete', name: 'Missing engine ID' },
      false
    ),
    original
  );
});

test('numeric IDs in persisted workflows match current string catalog IDs', () => {
  const legacy = { ...repository, id: 7 };
  const original = makeNode({ repoList: [legacy], repoIds: ['engine-id'] });
  const result = toggleKnowledgeSelection(
    original,
    { ...repository, id: '7' },
    true
  );
  assert.deepEqual(result.data.nodeParam.repoList, []);
  assert.deepEqual(result.data.nodeParam.repoIds, []);
  assert.equal(original.data.nodeParam.repoList[0].id, 7);
});

test('removal follows the stored descriptor engine ID rather than array position', () => {
  const other = { ...repository, id: 'other', coreRepoId: 'other-engine' };
  const original = makeNode({
    repoList: [repository, other],
    repoIds: ['other-engine', 'engine-id'],
  });
  const result = toggleKnowledgeSelection(
    original,
    { ...repository, coreRepoId: 'new-catalog-engine' },
    true
  );
  assert.deepEqual(result.data.nodeParam.repoIds, ['other-engine']);
  assert.deepEqual(result.data.nodeParam.repoList, [other]);
});

test('adding metadata to a legacy ID-only selection does not duplicate the engine binding', () => {
  const original = makeNode({ repoId: 'engine-id' });
  const result = toggleKnowledgeSelection(original, repository, false);
  assert.deepEqual(result.data.nodeParam.repoId, ['engine-id']);
  assert.deepEqual(result.data.nodeParam.repoList, [repository]);
});
