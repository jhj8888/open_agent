import { useEffect, useState } from 'react';
import { ApiScope, errorMessage } from './api';
import { ErrorBox, dateText, terminal } from './components';
import type { AgentRun } from './types';
import { cacheSummaryText, inputTokensText } from './tokens';

type ContentBlock = {
  text: string;
  source: string;
  availability: string;
  truncated: boolean;
  originalBytes: number;
};
type StepContent = {
  inputMode?: string;
  previousStepId?: string;
  availability?: string;
  input?: ContentBlock;
  thinking?: ContentBlock;
  toolCalls?: ContentBlock;
  toolResult?: ContentBlock;
  output?: ContentBlock;
};
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
  start_json: { summary: Record<string, string>; content?: StepContent };
  finish_json: {
    summary: Record<string, string>;
    content?: StepContent;
  } | null;
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
    cacheReadTokens?: number | null;
    uncachedInputTokens?: number | null;
    unknownCacheModelCalls?: number;
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
const inputModes: Record<string, string> = {
  initial: '首次输入',
  append: '本次新增消息',
  context_changed: '上下文已变化 · 当前上下文',
  tool_arguments: '本次工具参数',
};
function StepBody({ row }: { row: Step }): JSX.Element {
  const start = row.start_json.content;
  const end = row.finish_json?.content;
  if (!start && !end)
    return <p>此历史步骤未采集输入、think、工具结果与输出正文。</p>;
  const sections: [string, ContentBlock | undefined, string, boolean][] = [
    [
      `增量输入 · ${inputModes[start?.inputMode ?? ''] ?? '未记录'}`,
      start?.input,
      '未记录本次输入',
      start?.availability === 'run_limit',
    ],
    [
      'Think · 模型实际返回',
      end?.thinking,
      row.kind === 'model'
        ? row.status === 'running'
          ? '等待模型返回思考内容'
          : '模型未返回思考内容'
        : '此步骤不调用模型',
      end?.availability === 'run_limit',
    ],
    [
      '工具调用',
      end?.toolCalls,
      '本次没有模型工具调用',
      end?.availability === 'run_limit',
    ],
    [
      '工具调用结果',
      end?.toolResult,
      row.kind === 'tool'
        ? '尚无返回结果；错误分类见步骤摘要'
        : '请查看对应工具步骤',
      end?.availability === 'run_limit',
    ],
    [
      '输出',
      end?.output,
      row.kind === 'tool' ? '此步骤输出见「工具调用结果」' : '未记录输出正文',
      end?.availability === 'run_limit',
    ],
  ];
  return (
    <div className="step-content">
      {start?.previousStepId && (
        <small>
          输入比较基准：步骤 {start.previousStepId}
          ；上下文变化时显示当前输入，不标作增量。
        </small>
      )}
      {sections.map(([title, block, absent, limited]) => (
        <section key={title}>
          <strong>{title}</strong>
          {limited ? (
            <p>已达到本次任务的正文记录上限；用量、状态与产出文件继续记录。</p>
          ) : block &&
            (block.availability === 'provided' ||
              block.availability === 'partial') ? (
            <>
              {block.availability === 'partial' && (
                <small>执行中断前已返回的部分内容</small>
              )}
              {block.truncated && (
                <small>
                  内容较长，已截断展示（脱敏后原文{' '}
                  {block.originalBytes.toLocaleString()} 字节）
                </small>
              )}
              <pre>{block.text || '（空内容）'}</pre>
            </>
          ) : (
            <p>{absent}</p>
          )}
        </section>
      ))}
    </div>
  );
}

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
        模型用量逐次记录；输入总量 = 缓存命中 +
        新增（未命中缓存），新增不等于本轮新增消息长度。纯工具直接用量为
        0，框架父步骤不重复计入。展开模型或工具步骤，可查看脱敏后的输入、实际
        think、工具结果与输出；较长内容会标明截断。
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
            <span>
              {cacheSummaryText(
                page.totals.cacheReadTokens,
                page.totals.uncachedInputTokens,
                page.totals.unknownCacheModelCalls
              )}
            </span>
            {page.totals.unknownCacheModelCalls != null &&
              page.totals.unknownCacheModelCalls > 0 && (
                <span>
                  缓存明细未知 {page.totals.unknownCacheModelCalls} 次
                </span>
              )}
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
                    ? `${inputTokensText(row.input_tokens, row.cache_read_tokens)} / 输出 ${row.output_tokens}`
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
                    {row.kind === 'model' && (
                      <span>
                        {inputTokensText(
                          row.input_tokens,
                          row.cache_read_tokens
                        )}{' '}
                        Token；缓存与新增均已包含在输入总量中。
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
                    {(row.kind === 'model' || row.kind === 'tool') && (
                      <StepBody row={row} />
                    )}
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
