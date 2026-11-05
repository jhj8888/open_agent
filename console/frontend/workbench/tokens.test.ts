import assert from 'node:assert/strict';
import test from 'node:test';
import { cacheSummaryText, inputTokensText } from './tokens';

test('input usage partitions total input without double-counting cache', () => {
  assert.equal(
    inputTokensText(3213, 3072),
    '输入 3,213（缓存 3,072 · 新增 141）'
  );
  assert.equal(inputTokensText(3213, 0), '输入 3,213（缓存 0 · 新增 3,213）');
  assert.equal(
    inputTokensText(3213, 3213),
    '输入 3,213（缓存 3,213 · 新增 0）'
  );
  assert.equal(inputTokensText(0, 0), '输入 0（缓存 0 · 新增 0）');
});

test('missing or invalid cache is unknown, including old API responses', () => {
  for (const cached of [null, -1, 3214, 0.5, Number.NaN]) {
    assert.equal(
      inputTokensText(3213, cached),
      '输入 3,213（缓存未知 · 新增未知）'
    );
  }
  assert.equal(
    inputTokensText(null, null),
    '输入未报告（缓存未知 · 新增未知）'
  );
  assert.equal(
    cacheSummaryText(undefined, undefined, undefined),
    '缓存 / 新增 Token 明细未报告'
  );
});

test('aggregate counts show partial coverage and retain real zero', () => {
  assert.equal(cacheSummaryText(19, 17, 2), '已知缓存 19 · 已知新增 17 Token');
  assert.equal(cacheSummaryText(0, 12, 0), '缓存 0 · 新增 12 Token');
  assert.equal(cacheSummaryText(null, null, 3), '缓存 / 新增 Token 明细未报告');
});
