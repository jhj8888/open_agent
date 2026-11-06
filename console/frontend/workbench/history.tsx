import { useEffect, useState } from 'react';
import { aborted, ApiScope, errorMessage } from './api';
import { dateText, Empty, ErrorBox, Icon, Status } from './components';
import type { AgentRun } from './types';

export type HistoryRun = AgentRun & {
  deployment_id: string;
  agent_name: string;
};
export type HistoryPage = {
  items: HistoryRun[];
  next: string | null;
  hasMore: boolean;
};

export function TaskHistory({
  api,
  revision,
  openRun,
}: {
  api: ApiScope;
  revision: number;
  openRun: (run: HistoryRun) => void;
}): JSX.Element {
  const [query, setQuery] = useState({
    state: 'all',
    search: '',
    limit: 25,
    cursors: [''],
  });
  const [draft, setDraft] = useState('');
  const [reload, setReload] = useState(0);
  const [result, setResult] = useState<{
    key: string;
    page?: HistoryPage;
    error: string;
  }>();
  const [loading, setLoading] = useState(true);
  const before = query.cursors[query.cursors.length - 1]!;
  const params = new URLSearchParams({
    limit: String(query.limit),
    state: query.state,
    search: query.search,
  });
  if (before) params.set('before', before);
  const key = params.toString();
  const current = result?.key === key ? result : undefined;
  const refresh = (): void => setReload(value => value + 1);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    setLoading(true);
    void api
      .request<HistoryPage>(`${api.root}/runs?${key}`, 'GET', undefined, {
        signal: controller.signal,
      })
      .then(page => {
        if (!controller.signal.aborted) setResult({ key, page, error: '' });
      })
      .catch(error => {
        if (!controller.signal.aborted && !aborted(error))
          setResult({ key, error: errorMessage(error) });
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setLoading(false);
          timer = setTimeout(() => setReload(value => value + 1), 10000);
        }
      });
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [api, key, revision, reload]);
  const busy = loading || !current;
  return (
    <div className="panel task-history">
      <div className="panel-toolbar">
        <div className="category-tabs">
          {[
            ['all', '全部任务'],
            ['active', '进行中'],
            ['succeeded', '已完成'],
            ['failed', '失败与超时'],
            ['cancelled', '已取消'],
          ].map(([value, label]) => (
            <button
              key={value}
              className={query.state === value ? 'selected' : ''}
              onClick={() =>
                setQuery(previous => ({
                  ...previous,
                  state: value!,
                  cursors: [''],
                }))
              }
            >
              {label}
            </button>
          ))}
        </div>
        <button className="link" disabled={busy} onClick={refresh}>
          刷新
        </button>
      </div>
      <form
        className="history-search"
        onSubmit={event => {
          event.preventDefault();
          setQuery(previous => ({
            ...previous,
            search: draft.trim(),
            cursors: [''],
          }));
          refresh();
        }}
      >
        <input
          aria-label="搜索任务"
          placeholder="任务 ID 前缀或 Agent 名称"
          maxLength={128}
          value={draft}
          onChange={event => setDraft(event.target.value)}
        />
        <button className="button secondary" type="submit">
          搜索
        </button>
        {query.search && (
          <button
            className="link"
            type="button"
            onClick={() => {
              setDraft('');
              setQuery(previous => ({
                ...previous,
                search: '',
                cursors: [''],
              }));
            }}
          >
            清除搜索
          </button>
        )}
      </form>
      <ErrorBox message={current?.error ?? ''} retry={refresh} />
      <div className="table-scroll" aria-busy={busy}>
        <table>
          <thead>
            <tr>
              <th>任务 / Agent</th>
              <th>状态</th>
              <th>耗时</th>
              <th>Token</th>
              <th>创建时间</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {current?.page?.items.map(item => (
              <tr key={item.run_id}>
                <td>
                  <button className="task-link" onClick={() => openRun(item)}>
                    {item.agent_name}
                  </button>
                  <span className="subline mono" title={item.run_id}>
                    {item.run_id.slice(0, 8)}
                  </span>
                </td>
                <td>
                  <Status state={item.state} />
                </td>
                <td>
                  {item.started_at && item.finished_at
                    ? `${Math.max(0, (Date.parse(item.finished_at) - Date.parse(item.started_at)) / 1000).toFixed(1)} s`
                    : '—'}
                </td>
                <td>
                  {item.usage?.total_tokens?.toLocaleString() ?? '未报告'}
                </td>
                <td className="muted">{dateText(item.created_at)}</td>
                <td>
                  <button className="link" onClick={() => openRun(item)}>
                    查看详情 <Icon name="chevron" size={14} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!current?.page?.items.length && !current?.error && (
          <Empty
            text={busy ? '正在读取任务…' : '这个范围内还没有任务'}
            detail="可以调整状态或搜索条件，查看当前环境内有权访问的任务。"
          />
        )}
      </div>
      <div className="panel-toolbar history-pagination">
        <span className="muted" aria-live="polite">
          第 {query.cursors.length} 页 · 本页 {current?.page?.items.length ?? 0}{' '}
          条
        </span>
        <div className="heading-actions">
          <select
            aria-label="每页任务数"
            value={query.limit}
            onChange={event =>
              setQuery(previous => ({
                ...previous,
                limit: Number(event.target.value),
                cursors: [''],
              }))
            }
          >
            {[10, 25, 50].map(size => (
              <option key={size} value={size}>
                每页 {size} 条
              </option>
            ))}
          </select>
          <button
            className="link"
            disabled={query.cursors.length === 1}
            onClick={() => {
              setQuery(previous => ({ ...previous, cursors: [''] }));
              refresh();
            }}
          >
            返回最新
          </button>
          <button
            className="button secondary"
            disabled={query.cursors.length === 1 || busy}
            onClick={() =>
              setQuery(previous => ({
                ...previous,
                cursors: previous.cursors.slice(0, -1),
              }))
            }
          >
            上一页
          </button>
          <button
            className="button secondary"
            disabled={busy || !current?.page?.hasMore || !current.page.next}
            onClick={() => {
              const next = current?.page?.next;
              if (next)
                setQuery(previous => ({
                  ...previous,
                  cursors: [...previous.cursors, next],
                }));
            }}
          >
            下一页
          </button>
        </div>
      </div>
      <div className="inline-note">
        每页 {query.limit} 条，当前页每 10
        秒更新。翻页可查看全部保留历史；新任务请返回最新页查看。
      </div>
    </div>
  );
}
