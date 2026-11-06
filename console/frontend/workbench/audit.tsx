import { useEffect, useState } from 'react';
import { ApiScope, aborted, errorMessage } from './api';
import { dateText, Empty, ErrorBox, Modal } from './components';
import type { AuditEntry } from './types';

const actions: Record<string, string> = {
  'tenant.created': '创建租户',
  'tenant.status_changed': '变更租户状态',
  'tenant.quota_changed': '调整配额',
  'tenant.member_changed': '调整成员角色',
  'tenant.member_removed': '移除成员',
  'tenant.admin_enrolled': '登记环境管理员',
  'model.version.created': '更新模型配置',
  'model.probe.completed': '验证模型能力',
  'deployment.created': '创建 Agent 部署',
  'deployment.published': '发布 Agent',
  'deployment.disabled': '停用 Agent',
  'deployment.key.issued': '签发调用凭据',
  'deployment.key.rotated': '轮换调用凭据',
  'deployment.key.revoked': '吊销调用凭据',
};

function auditValue(value: unknown): string {
  if (value == null) return '无记录';
  if (typeof value === 'string') {
    if (value.length > 50000)
      return value.slice(0, 50000) + '\n（内容较长，已截断显示）';
    try {
      return JSON.stringify(JSON.parse(value), null, 2);
    } catch {
      return value;
    }
  }
  return JSON.stringify(value, null, 2).slice(0, 50000);
}

export function AuditHistory({
  api,
  enabled,
  revision,
}: {
  api: ApiScope;
  enabled: boolean;
  revision: number;
}): JSX.Element {
  const [query, setQuery] = useState({ action: '', cursors: [''] });
  const [reload, setReload] = useState(0);
  const [result, setResult] = useState<{
    key: string;
    rows?: AuditEntry[];
    error: string;
  }>();
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<AuditEntry>();
  const params = new URLSearchParams({ action: query.action });
  const before = query.cursors.at(-1)!;
  if (before) params.set('beforeId', before);
  const key = params.toString();
  const current = enabled && result?.key === key ? result : undefined;
  useEffect(() => {
    setSelected(undefined);
    if (!enabled) {
      setResult(undefined);
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    void api
      .request<AuditEntry[]>(`${api.root}/audit?${key}`, 'GET', undefined, {
        signal: controller.signal,
      })
      .then(rows => {
        if (!controller.signal.aborted) setResult({ key, rows, error: '' });
      })
      .catch(error => {
        if (!controller.signal.aborted && !aborted(error))
          setResult({ key, error: errorMessage(error) });
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [api, enabled, key, revision, reload]);
  const next = current?.rows?.at(-1)?.nextBeforeId;
  return (
    <div className="panel recent audit-history">
      <div className="panel-heading">
        <h2>审计记录</h2>
        <button
          className="link"
          disabled={!enabled || loading}
          onClick={() => {
            setQuery(previous => ({ ...previous, cursors: [''] }));
            setReload(value => value + 1);
          }}
        >
          刷新最新
        </button>
      </div>
      {!enabled ? (
        <Empty
          text="当前角色没有审计查看权限"
          detail="请联系当前环境的租户管理员。"
        />
      ) : (
        <>
          <div className="audit-filter">
            <label>
              操作类型{' '}
              <select
                aria-label="审计操作类型"
                value={query.action}
                onChange={event =>
                  setQuery({ action: event.target.value, cursors: [''] })
                }
              >
                <option value="">全部操作</option>
                {Object.entries(actions).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <span className="muted">每页最多 100 条 · 当前租户 / 环境</span>
          </div>
          <ErrorBox
            message={current?.error ?? ''}
            retry={() => setReload(value => value + 1)}
          />
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>操作</th>
                  <th>操作人</th>
                  <th>对象</th>
                  <th>时间</th>
                  <th>变更</th>
                </tr>
              </thead>
              <tbody>
                {current?.rows?.map(item => (
                  <tr key={item.cursor ?? item.id}>
                    <td>{actions[item.action] ?? item.action}</td>
                    <td className="mono small">{item.actorUid}</td>
                    <td className="mono small">{item.subjectId}</td>
                    <td>{dateText(item.createdAt)}</td>
                    <td>
                      <button
                        className="link"
                        onClick={() => setSelected(item)}
                      >
                        查看变更
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {loading && (
            <div className="info-strip" role="status">
              正在读取审计记录…
            </div>
          )}
          {!loading && current?.rows?.length === 0 && (
            <Empty text="没有符合条件的审计记录" />
          )}
          <div className="audit-pagination">
            <span>
              第 {query.cursors.length} 页 · 本页 {current?.rows?.length ?? 0}{' '}
              条
            </span>
            <div className="row-actions">
              <button
                className="button"
                disabled={loading || query.cursors.length === 1}
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
                className="button"
                disabled={loading || !next}
                onClick={() => {
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
        </>
      )}
      {enabled && selected && (
        <Modal
          title={actions[selected.action] ?? '审计详情'}
          wide
          onClose={() => setSelected(undefined)}
        >
          <div className="form-body">
            <p>
              操作人：<span className="mono">{selected.actorUid}</span>
            </p>
            <p>
              对象：<span className="mono">{selected.subjectId}</span>
            </p>
            <p>
              {dateText(selected.createdAt)} · 至少保留至{' '}
              {selected.retainUntil ? dateText(selected.retainUntil) : '未提供'}
            </p>
            <div className="audit-values">
              <section>
                <h3>变更前</h3>
                <pre>{auditValue(selected.beforeValue)}</pre>
              </section>
              <section>
                <h3>变更后 / 执行结果</h3>
                <pre>{auditValue(selected.afterValue)}</pre>
              </section>
            </div>
            <p className="form-note">
              仅展示服务端保存的审计内容。修改保留天数不会缩短已有记录的保留时间。
            </p>
          </div>
        </Modal>
      )}
    </div>
  );
}
