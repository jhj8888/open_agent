import assert from 'node:assert/strict';
import test from 'node:test';
import {
  iteratorCanvasNodes,
  leftmostPosition,
  workflowSelection,
} from './graph-state.ts';
import { createResetNodes } from '../clear-flow-canvas/reset-nodes.ts';

const node = (id, position, data = {}) => ({
  id,
  nodeType: 'code',
  position,
  data: { inputs: [], outputs: [], nodeParam: {}, ...data },
});

test('selection resolves generic graph IDs to current typed store records', () => {
  const current = node('selected', { x: 10, y: 20 }, { label: 'latest' });
  const edge = { id: 'edge', source: 'selected', target: 'other' };
  const selected = workflowSelection(
    {
      nodes: [{ id: 'selected', data: { label: 'stale' } }, { id: 'deleted' }],
      edges: [{ id: 'edge' }, { id: 'missing' }],
    },
    { nodes: [current], edges: [edge] }
  );
  assert.deepEqual(selected, { nodes: [current], edges: [edge] });
  assert.equal(selected.nodes[0], current);
});

test('iterator expansion restores coordinates and safely retains legacy positions', () => {
  const original = node(
    'restored',
    { x: 10, y: 20 },
    { parentId: 'iterator', originPosition: { x: 300, y: 400 } }
  );
  const legacy = node('legacy', { x: 50, y: 60 }, { parentId: 'iterator' });
  const result = iteratorCanvasNodes(
    [original, legacy, node('other', { x: 1, y: 2 })],
    'iterator',
    true
  );
  assert.deepEqual(
    result.map(item => item.position),
    [
      { x: 300, y: 400 },
      { x: 50, y: 60 },
    ]
  );
  assert.equal(result[0].data.parentId, '');
  assert.equal(result[0].draggable, false);
  assert.equal(original.data.parentId, 'iterator');
  assert.equal(leftmostPosition([]), undefined);
  assert.deepEqual(leftmostPosition(result), { x: 50, y: 60 });
});

test('canvas reset copies the catalog and creates fresh node and parameter IDs', () => {
  const templates = [
    {
      idType: 'node-start',
      position: { x: 123, y: 456 },
      data: {
        nodeParam: {},
        inputs: [],
        outputs: [
          { id: 'template-output', name: 'output', schema: { type: 'string' } },
        ],
      },
    },
    {
      idType: 'node-end',
      data: {
        nodeParam: {},
        inputs: [
          {
            id: 'template-input',
            name: 'input',
            schema: { type: 'string', value: { type: 'ref', content: {} } },
          },
        ],
        outputs: [],
      },
    },
  ];
  const original = structuredClone(templates);
  let counter = 0;
  const createId = type => `${type || 'parameter'}-${++counter}`;
  const first = createResetNodes(templates, createId, createId);
  const second = createResetNodes(templates, createId, createId);
  assert.deepEqual(templates, original);
  assert.notEqual(first[0].id, second[0].id);
  assert.notEqual(first[0].data.outputs[0].id, second[0].data.outputs[0].id);
  assert.deepEqual(first[0].position, { x: 123, y: 456 });
  assert.deepEqual(first[1].position, { x: 1000, y: 300 });
  first[0].data.outputs[0].name = 'edited';
  assert.equal(templates[0].data.outputs[0].name, 'output');
});

test('an incomplete catalog cannot replace the current canvas with an empty graph', () => {
  const unreachable = () => {
    throw new Error('must not instantiate');
  };
  assert.equal(
    createResetNodes(undefined, unreachable, unreachable),
    undefined
  );
  assert.equal(createResetNodes([], unreachable, unreachable), undefined);
  assert.equal(
    createResetNodes([{ idType: 'node-start' }], unreachable, unreachable),
    undefined
  );
});
