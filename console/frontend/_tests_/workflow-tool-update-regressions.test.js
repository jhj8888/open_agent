import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const react = require('react');
const source = readFileSync(
  new URL(
    '../src/components/workflow/hooks/use-one-click-update.tsx',
    import.meta.url
  ),
  'utf8'
);
const compiled = ts.transpileModule(source, {
  fileName: 'use-one-click-update.tsx',
  compilerOptions: {
    target: ts.ScriptTarget.ES2020,
    module: ts.ModuleKind.CommonJS,
    jsx: ts.JsxEmit.React,
    esModuleInterop: true,
  },
}).outputText;

async function upgradeAgentTools(tools, selectedIds, versions) {
  let node = {
    id: 'agent::fixture',
    data: {
      nodeParam: {
        plugin: {
          tools,
          toolsList: selectedIds.map(toolId => ({
            toolId,
            type: 'tool',
            name: toolId,
            isLatest: false,
          })),
        },
      },
      inputs: [],
      outputs: [],
    },
  };
  const requests = [];
  let saveCount = 0;
  const graph = {
    setNode: (_id, update) => {
      node = update(node);
    },
  };
  const manager = {
    getCurrentStore: () => selector => selector(graph),
    autoSaveCurrentFlow: () => {
      saveCount += 1;
    },
    canPublishSetNot: () => {},
  };
  const mocks = {
    react: {
      ...react,
      useMemo: callback => callback(),
      useCallback: callback => callback,
    },
    'react-i18next': { useTranslation: () => ({ t: key => key }) },
    antd: { Popconfirm: 'popconfirm' },
    '@/components/workflow/store/use-flows-manager': selector =>
      selector(manager),
    '@/services/plugin': {
      getToolLatestVersion: async ids => {
        requests.push(ids);
        return versions;
      },
    },
    '@/utils': {},
    '@/services/flow': {},
    '@/services/rpa': {},
    '@/utils/rpa': {},
    '../utils/reactflowUtils': {},
  };
  const module = { exports: {} };
  new Function('require', 'module', 'exports', compiled)(
    name =>
      Object.hasOwn(mocks, name)
        ? mocks[name]
        : name.startsWith('@/assets/')
          ? name
          : require(name),
    module,
    module.exports
  );
  const view = module.exports.AgentNodeOneClickUpdate({
    id: node.id,
    data: node.data,
  });
  view.props.children.props.onConfirm();
  await new Promise(resolve => setImmediate(resolve));
  return {
    tools: node.data.nodeParam.plugin.tools,
    toolsList: node.data.nodeParam.plugin.toolsList,
    requests,
    saveCount,
  };
}

test('updating one tool preserves unselected legacy strings, versionless objects and metadata', async () => {
  const untouched = [
    'legacy-string',
    { tool_id: 'legacy-object' },
    { tool_id: 'pinned', version: 'V3.0', metadata: { label: 'keep' } },
  ];
  const result = await upgradeAgentTools(
    [...untouched, { tool_id: 'selected', version: 'V1.0' }],
    ['selected'],
    { selected: 'V2.0' }
  );
  assert.deepEqual(result.tools, [
    ...untouched,
    { tool_id: 'selected', version: 'V2.0' },
  ]);
  assert.deepEqual(result.requests, [['selected']]);
  assert.equal(result.saveCount, 1);
});

test('selected legacy strings and versionless objects receive their returned versions exactly once', async () => {
  const result = await upgradeAgentTools(
    [
      'selected-string',
      { tool_id: 'selected-object' },
      { tool_id: 'untouched', version: 'V4.0' },
    ],
    ['selected-string', 'selected-object'],
    { 'selected-string': 'V2.0', 'selected-object': 'V3.0' }
  );
  assert.deepEqual(result.tools, [
    { tool_id: 'untouched', version: 'V4.0' },
    { tool_id: 'selected-string', version: 'V2.0' },
    { tool_id: 'selected-object', version: 'V3.0' },
  ]);
  assert.deepEqual(result.requests, [['selected-string', 'selected-object']]);
  assert.equal(result.saveCount, 1);
});

test('partial version responses preserve missing tools and do not mark them up to date', async () => {
  const result = await upgradeAgentTools(
    [
      { tool_id: 'selected', version: 'V1.0' },
      'missing',
      { tool_id: 'empty-version' },
    ],
    ['selected', 'missing', 'empty-version'],
    { selected: 'V2.0', 'empty-version': '', unexpected: 'V9.0' }
  );
  assert.deepEqual(result.tools, [
    'missing',
    { tool_id: 'empty-version' },
    { tool_id: 'selected', version: 'V2.0' },
  ]);
  assert.deepEqual(
    result.toolsList.map(tool => [tool.toolId, tool.isLatest]),
    [
      ['selected', true],
      ['missing', false],
      ['empty-version', false],
    ]
  );
});
