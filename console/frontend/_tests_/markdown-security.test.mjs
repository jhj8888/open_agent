import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ReactMarkdown from 'react-markdown';
import rehypeKatex from 'rehype-katex';
import rehypeRaw from 'rehype-raw';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import {
  markdownKatexOptions,
  markdownSanitizePlugin,
} from '../src/components/markdown-sanitize.ts';

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');

function renderMarkdown(markdown) {
  return renderToStaticMarkup(
    React.createElement(ReactMarkdown, {
      children: markdown,
      remarkPlugins: [remarkMath, remarkGfm],
      rehypePlugins: [
        rehypeRaw,
        markdownSanitizePlugin,
        [rehypeKatex, markdownKatexOptions],
      ],
    })
  );
}

test('Markdown removes active HTML, event handlers, and dangerous URLs', () => {
  const html = renderMarkdown(`
<iframe src="javascript:alert(1)" srcdoc="<img src=x onerror=alert(1)>"></iframe>
<style>body { display: none }</style>
<meta http-equiv="refresh" content="0;url=javascript:alert(1)">
<form action="javascript:alert(1)"><button>submit</button></form>
<img src="data:text/html,<script>alert(1)</script>" onerror="alert(1)" style="position:fixed">
<a href="java&#x73;cript:alert(1)" onclick="alert(1)">raw bad link</a>

[markdown bad link](javascript:alert(1))

<strong>safe HTML</strong>

[safe link](https://example.com/path)
`);

  assert.doesNotMatch(
    html,
    /<(?:iframe|style|meta|script|form|object|embed|link|base)\b/i
  );
  assert.doesNotMatch(html, /\b(?:srcdoc|onerror|onclick|style)=/i);
  assert.doesNotMatch(html, /javascript:|data:text\/html/i);
  assert.match(html, /<strong>safe HTML<\/strong>/);
  assert.match(html, /href="https:\/\/example\.com\/path"/);
});

test('Markdown keeps GFM, fenced-code, and KaTeX output', () => {
  const html = renderMarkdown(`
| A | B |
| - | - |
| 1 | 2 |

\`\`\`javascript
const safe = true;
\`\`\`

Inline math: $x^2$
`);

  assert.match(html, /<table>/);
  assert.match(html, /class="language-javascript"/);
  assert.match(html, /class="katex"/);
});

test('KaTeX cannot reintroduce trusted HTML or dangerous links', () => {
  const html = renderMarkdown(String.raw`
$\href{javascript:alert(1)}{bad}$
$\htmlStyle{position:fixed}{bad}$
$\includegraphics{javascript:alert(2)}$
`);

  assert.doesNotMatch(html, /<(?:a|img)\b/i);
  assert.doesNotMatch(html, /\b(?:href|src)=/i);
  assert.doesNotMatch(html, /\bstyle="[^"]*position\s*:\s*fixed/i);
});

test('knowledge names render malicious HTML as inert text', () => {
  const maliciousName =
    '<img src=x onerror="alert(1)"><script>alert(2)</script>';
  const html = renderToStaticMarkup(
    React.createElement('span', { title: maliciousName }, maliciousName)
  );

  const componentSource = readFileSync(
    resolve(frontendRoot, 'src/components/knowledge-name-text/index.tsx'),
    'utf8'
  );
  const callsiteSources = [
    'src/components/workflow/modal/knowledge-detail/index.tsx',
    'src/pages/resource-management/knowledge-detail/document-page/hooks/use-columns.tsx',
  ].map(path => readFileSync(resolve(frontendRoot, path), 'utf8'));

  assert.doesNotMatch(html, /<img\b|<script\b/i);
  assert.match(html, /&lt;img src=x onerror=/);
  assert.match(html, /&lt;script&gt;alert\(2\)&lt;\/script&gt;/);
  assert.match(componentSource, /\{name\}/);
  assert.doesNotMatch(componentSource, /dangerouslySetInnerHTML/);
  for (const source of callsiteSources) {
    assert.match(source, /<KnowledgeNameText name=\{name\} \/>/);
    assert.doesNotMatch(
      source,
      /dangerouslySetInnerHTML=\{\{ __html: name \}\}/
    );
  }
});
