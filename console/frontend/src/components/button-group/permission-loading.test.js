import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import { createRequire } from 'node:module';
import ts from 'typescript';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
const require = createRequire(import.meta.url);
let permissionParams;

function loadComponent(relativePath) {
  const source = fs.readFileSync(
    new URL(relativePath, import.meta.url),
    'utf8'
  );
  const compiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      jsx: ts.JsxEmit.React,
      esModuleInterop: true,
      target: ts.ScriptTarget.ES2020,
    },
  }).outputText;
  const module = { exports: {} };
  const imports = id => {
    if (id === '@/hooks/use-user-store')
      return { useUserStoreHook: () => ({ permissionParams }) };
    if (id === 'react-i18next')
      return { useTranslation: () => ({ t: value => value }) };
    if (id === '@/permissions/utils')
      return {
        hasModulePermission: () => false,
        checkResourceRestrictions: () => false,
      };
    if (id === './types')
      return {
        PermissionFailureBehavior: { DISABLE: 'disable', HIDE: 'hide' },
      };
    if (id.endsWith('.scss')) return {};
    if (id === 'antd')
      return {
        Button: ({ disabled, children }) =>
          React.createElement('button', { disabled }, children),
        Tooltip: ({ children }) => children,
      };
    return require(id);
  };
  new Function('require', 'module', 'exports', compiled)(
    imports,
    module,
    module.exports
  );
  return module.exports.default;
}
const SpaceButton = loadComponent('./space-button.tsx');
const SpaceTab = loadComponent('../space/space-tab/index.tsx');
const role = { spaceType: 'team', roleType: 'admin' };

test('permission-dependent buttons remain disabled until a real role is available', () => {
  const config = {
    key: 'manage',
    text: 'Manage',
    permission: { customCheck: () => true },
  };
  permissionParams = undefined;
  assert.match(
    renderToStaticMarkup(React.createElement(SpaceButton, { config })),
    /disabled/
  );
  assert.doesNotMatch(
    renderToStaticMarkup(
      React.createElement(SpaceButton, {
        config: { key: 'public', text: 'Public' },
      })
    ),
    /disabled/
  );
  permissionParams = role;
  assert.doesNotMatch(
    renderToStaticMarkup(React.createElement(SpaceButton, { config })),
    /disabled/
  );
});

test('protected tabs and role-based visibility stay hidden while user state is loading', () => {
  permissionParams = undefined;
  assert.equal(
    renderToStaticMarkup(
      React.createElement(SpaceTab, {
        options: [
          {
            key: 'protected',
            label: 'Protected',
            permission: { customCheck: () => true },
          },
        ],
      })
    ),
    ''
  );
  assert.equal(
    renderToStaticMarkup(
      React.createElement(SpaceButton, {
        config: { key: 'visible', text: 'Visible', visible: () => true },
      })
    ),
    ''
  );
  permissionParams = role;
  assert.match(
    renderToStaticMarkup(
      React.createElement(SpaceTab, {
        options: [
          {
            key: 'protected',
            label: 'Protected',
            permission: { customCheck: () => true },
          },
        ],
      })
    ),
    /Protected/
  );
});
