/** Cache hits are a subset of input, not additional usage or cache writes. */
export function inputTokensText(
  input: number | null,
  cached: number | null
): string {
  const validInput = input != null && Number.isSafeInteger(input) && input >= 0;
  const validCache =
    validInput &&
    cached != null &&
    Number.isSafeInteger(cached) &&
    cached >= 0 &&
    cached <= input;
  if (!validInput) return '输入未报告（缓存未知 · 新增未知）';
  const total = input.toLocaleString('zh-CN');
  if (!validCache) return `输入 ${total}（缓存未知 · 新增未知）`;
  return `输入 ${total}（缓存 ${cached.toLocaleString('zh-CN')} · 新增 ${(input - cached).toLocaleString('zh-CN')}）`;
}

export function cacheSummaryText(
  cached: number | null | undefined,
  fresh: number | null | undefined,
  unknownCalls: number | undefined
): string {
  if (cached == null || fresh == null) return '缓存 / 新增 Token 明细未报告';
  const partial = unknownCalls == null || unknownCalls > 0;
  return `${partial ? '已知' : ''}缓存 ${cached.toLocaleString('zh-CN')} · ${partial ? '已知' : ''}新增 ${fresh.toLocaleString('zh-CN')} Token`;
}
