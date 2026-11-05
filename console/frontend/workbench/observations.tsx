import { useEffect, useState } from 'react';
import { ApiScope, errorMessage } from './api';
import { ErrorBox, dateText, terminal } from './components';
import type { AgentRun } from './types';

type Step = {
  id: number;
  attempt_no: number;
  step_id: string;
  parent_step_id: string | null;
  kind: string;
  name: string;
  status: string;
  started_at: string;
  ended_at: string | null;
  duration_ms: number | null;
  usage_source: string;
  input_tokens: number | null;
  output_tokens: number | null;
  cache_read_tokens: number | null;
  cache_write_tokens: number | null;
  start_json: { summary: Record<string, string> };
  finish_json: { summary: Record<string, string> } | null;
};
type Page = {
  items: Step[];
  hasMore: boolean;
  next: number;
  totals: {
    steps: number;
    modelCalls: number;
    toolCalls: number;
    unknownModelCalls: number;
    inputTokens: number;
    outputTokens: number;
  };
};
const kinds: Record<string, string> = {
  agent: 'Agent',
  chain: '框架步骤',
  model: '模型调用',
  tool: '工具调用',
  skill: '加载 Skill',
  artifact: '归档文件',
};
const statuses: Record<string, string> = {
  running: '运行中',
  succeeded: '已完成',
  failed: '失败',
  interrupted: '中断 / 未完整上报',
};
const summaries: Record<string, string> = {
  input: '输入摘要',
  output: '输出摘要',
  errorType: '错误分类',
  toolCallId: '工具调用 ID',
  releaseId: 'Skill 版本',
  manifestSha256: '清单摘要',
  fileCount: '文件数量',
};

export function TaskObservations({
  api,
  run,
}: {
  api: ApiScope;
  run: AgentRun;
}): JSX.Element {
  const [rows, setRows] = useState<Step[]>([]);
  const [page, setPage] = useState<Page>();
  const [error, setError] = useState('');
  const [filter, setFilter] = useState('key');
  const [limit, setLimit] = useState(200);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = async (): Promise<void> => {
      try {
        let after = 0;
        const loaded: Step[] = [];
        let result: Page;
        do {
          result = await api.request<Page>(
            `/api/v1/runs/${run.run_id}/observations?after=${after}&limit=200`,
            'GET',
            undefined,
            { signal: controller.signal }
          );
          loaded.push(...result.items);
          after = result.next;
        } while (
          result.hasMore &&
          loaded.length < limit &&
          !controller.signal.aborted
        );
        if (controller.signal.aborted) return;
        setRows(loaded);
        setPage(result);
        setError('');
      } catch (failure) {
        if (!controller.signal.aborted) setError(errorMessage(failure));
      }
      if (!controller.signal.aborted && !terminal(run.state))
        timer = setTimeout(() => {
          void load();
        }, 5000);
    };
    void load();
    return () => {
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, [api, run.run_id, run.state, limit, refresh]);
  const visible = rows.filter(
    row =>
      filter === 'all' ||
      (filter === 'key' ? row.kind !== 'chain' : row.kind === filter)
  );
  const root = rows.find(row => row.kind === 'agent');
  const rangeStart = root ? new Date(root.started_at).getTime() : 0;
  const range = Math.max(1, root?.duration_ms ?? Date.now() - rangeStart);
  return (
    <section
      className="task-outputs task-observations"
      aria-label="Agent 执行过程"
    >
      <div className="output-heading">
        <strong>Agent 执行过程</strong>
        <span>{page?.totals.steps ?? 0} 个步骤</span>
      </div>
      <p className="form-note">
        模型用量逐次记录，缓存包含在输入 Token 中；纯工具直接用量为
        0，框架父步骤不重复计入。摘要仅包含结构与长度。
      </p>
      <ErrorBox message={error} />
      {page && page.totals.steps > 0 ? (
        <>
          <div className="observation-summary">
            <span>模型 {page.totals.modelCalls} 次</span>
            <span>工具 {page.totals.toolCalls} 次</span>
            <span>
              已报告 Token{' '}
              {(
                Number(page.totals.inputTokens) +
                Number(page.totals.outputTokens)
              ).toLocaleString()}
            </span>
            <span>未知用量 {page.totals.unknownModelCalls} 次</span>
          </div>
          <div className="output-heading">
            <select
              aria-label="执行步骤类型"
              value={filter}
              onChange={event => setFilter(event.target.value)}
            >
              <option value="key">关键步骤</option>
              <option value="model">模型调用</option>
              <option value="tool">工具调用</option>
              <option value="all">全部框架步骤</option>
            </select>
            <button
              className="link"
              onClick={() => setRefresh(value => value + 1)}
            >
              刷新步骤
            </button>
          </div>
          <div className="step-timeline">
            {visible.map(row => {
              const offset = Math.max(
                0,
                new Date(row.started_at).getTime() - rangeStart
              );
              const duration = row.duration_ms;
              const token =
                row.kind === 'model'
                  ? row.usage_source === 'provider'
                    ? `输入 ${row.input_tokens} / 输出 ${row.output_tokens}`
                    : 'Token 未报告'
                  : row.usage_source === 'aggregate'
                    ? '父步骤不重复计量'
                    : '直接 Token 0';
              return (
                <details className="step-row" key={row.id}>
                  <summary>
                    <span className={`step-dot ${row.status}`} />
                    <span className="step-title">
                      <b>
                        {kinds[row.kind]} · {row.name}
                      </b>
                      <small>
                        {statuses[row.status]} ·{' '}
                        {duration == null
                          ? '耗时未完整记录'
                          : `${(duration / 1000).toFixed(3)} s`}{' '}
                        · {token}
                      </small>
                      <span className="step-track">
                        <span
                          style={{
                            marginLeft: `${Math.min(99, (offset / range) * 100)}%`,
                            width: `${Math.max(1, Math.min(100, ((duration ?? 0) / range) * 100))}%`,
                          }}
                        />
                      </span>
                    </span>
                  </summary>
                  <div className="step-detail">
                    <span>
                      {dateText(row.started_at)} →{' '}
                      {row.ended_at ? dateText(row.ended_at) : '未记录结束'}
                    </span>
                    <span>
                      步骤 {row.step_id} · 尝试 {row.attempt_no}
                    </span>
                    <span>父步骤 {row.parent_step_id ?? '无'}</span>
                    {row.cache_read_tokens != null && (
                      <span>
                        缓存读取 {row.cache_read_tokens} Token（已含于输入）
                      </span>
                    )}
                    {row.cache_write_tokens != null && (
                      <span>缓存写入 {row.cache_write_tokens} Token</span>
                    )}
                    {Object.entries({
                      ...row.start_json.summary,
                      ...row.finish_json?.summary,
                    }).map(([key, value]) => (
                      <span key={key}>
                        <b>{summaries[key] ?? key}：</b>
                        {value}
                      </span>
                    ))}
                  </div>
                </details>
              );
            })}
          </div>
          {page.hasMore && (
            <button
              className="secondary"
              onClick={() => setLimit(value => value + 200)}
            >
              加载更多步骤（已加载 {rows.length}）
            </button>
          )}
        </>
      ) : (
        <p className="form-note">
          {terminal(run.state)
            ? '此任务未采集逐步观测数据；已有日志和产物仍可查看。'
            : '正在等待第一条执行步骤。'}
        </p>
      )}
    </section>
  );
}
