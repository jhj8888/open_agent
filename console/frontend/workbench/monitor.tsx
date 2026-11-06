import React, { useEffect, useState } from 'react';
import { ApiScope } from './api';
import { dateText, ErrorBox, Icon, Status, useResource } from './components';
import { ObservationStatistics } from './observation-statistics';

type Statistics = {
  window: string;
  windowStart: string;
  windowEnd: string;
  sampledAt: string;
  totals: {
    total: number;
    reportedUsageRuns: number;
    totalTokens: number | null;
    promptTokens: number | null;
    completionTokens: number | null;
    meanQueueSeconds: number | null;
    meanRunSeconds: number | null;
    startedRuns: number;
    timedRuns: number;
  };
  states: { state: string; count: number }[];
  current: {
    queued: number;
    running: number;
    cancelling: number;
    oldestQueueSeconds: number;
    occupiedExecutionSlots: number;
    unreleasedSandboxes: number;
    cleanupPendingSandboxes: number;
  };
};
type Host = {
  available: boolean;
  reason?: string;
  sampledAt?: string;
  values?: Record<string, number>;
};
const number = (value: number | undefined | null, suffix = '') =>
  value == null
    ? '未报告'
    : `${value.toLocaleString(undefined, { maximumFractionDigits: 1 })}${suffix}`;

export function MonitorPage({
  api,
  platformAdmin,
}: {
  api: ApiScope;
  platformAdmin: boolean;
}): JSX.Element {
  const [window, setWindow] = useState('24h');
  const statistics = useResource(
    () => api.list<Statistics>(`statistics?window=${window}`),
    [api, window]
  );
  const host = useResource(
    () =>
      platformAdmin
        ? api.request<Host>('/platform/operations/host-metrics')
        : Promise.resolve(null),
    [api, platformAdmin]
  );
  useEffect(() => {
    const timer = setInterval(() => {
      statistics.refresh();
      if (platformAdmin) host.refresh();
    }, 30000);
    return () => clearInterval(timer);
  }, [api, window, platformAdmin]);
  const data = statistics.data?.window === window ? statistics.data : undefined;
  const totals = data?.totals;
  return (
    <div className="monitor-page">
      <ObservationStatistics
        key={`${api.tenant}-${api.environment}`}
        api={api}
      />
      <div className="panel-heading">
        <div>
          <h2>任务运行概况</h2>
          <p className="muted">
            统计当前租户与环境中的历史任务，包含已删除任务的计量记录，每 30
            秒刷新。
          </p>
        </div>
        <div className="top-actions">
          <select
            aria-label="统计时间范围"
            value={window}
            onChange={event => setWindow(event.target.value)}
          >
            <option value="1h">最近 1 小时</option>
            <option value="24h">最近 24 小时</option>
            <option value="7d">最近 7 天</option>
          </select>
          <button
            className="button"
            onClick={() => {
              statistics.refresh();
              if (platformAdmin) host.refresh();
            }}
          >
            <Icon name="refresh" size={16} /> 刷新监控
          </button>
        </div>
      </div>
      <ErrorBox message={statistics.error} retry={statistics.refresh} />
      {statistics.loading && !data && (
        <div className="info-strip">正在读取统计…</div>
      )}
      {data && (
        <>
          <div className="info-strip">
            {dateText(data.windowStart)} — {dateText(data.windowEnd)} ·
            已报告用量 {totals!.reportedUsageRuns} / {totals!.total}{' '}
            个任务；缺少用量不计为零。
          </div>
          <div className="stats-grid">
            {[
              ['窗口内任务', number(totals!.total), '包含全部部署与执行状态'],
              [
                '已报告 Token',
                number(totals!.totalTokens),
                `输入 ${number(totals!.promptTokens)} · 输出 ${number(totals!.completionTokens)}`,
              ],
              [
                '平均排队',
                number(totals!.meanQueueSeconds, ' s'),
                `${totals!.startedRuns} 个已开始任务`,
              ],
              [
                '平均执行',
                number(totals!.meanRunSeconds, ' s'),
                `${totals!.timedRuns} 个具有完整耗时的任务`,
              ],
            ].map(([label, value, detail]) => (
              <div className="stat-card" key={label}>
                <div>
                  <span>{label}</span>
                </div>
                <strong>{value}</strong>
                <footer>{detail}</footer>
              </div>
            ))}
          </div>
          <div className="two-columns">
            <section className="panel resource-panel">
              <div className="panel-heading">
                <h2>任务状态分布</h2>
              </div>
              <div className="status-chart">
                {[
                  'queued',
                  'running',
                  'cancel_requested',
                  'succeeded',
                  'failed',
                  'cancelled',
                  'timed_out',
                ].map(state => {
                  const count =
                    data.states.find(row => row.state === state)?.count ?? 0;
                  return (
                    <div key={state}>
                      <Status state={state} />
                      <div className="progress-track">
                        <span
                          style={{
                            width: `${totals!.total ? (count / totals!.total) * 100 : 0}%`,
                          }}
                        />
                      </div>
                      <b>{count}</b>
                    </div>
                  );
                })}
              </div>
            </section>
            <section className="panel resource-panel">
              <div className="panel-heading">
                <h2>当前占用</h2>
                <span className="muted">不受时间窗口限制</span>
              </div>
              <div className="status-chart">
                {[
                  ['排队任务', data.current.queued],
                  ['运行任务', data.current.running],
                  ['取消处理中', data.current.cancelling],
                  ['占用执行槽', data.current.occupiedExecutionSlots],
                  ['未释放沙盒', data.current.unreleasedSandboxes],
                  ['待回收沙盒', data.current.cleanupPendingSandboxes],
                ].map(([label, value]) => (
                  <div key={label}>
                    <span>{label}</span>
                    <span className="metric-spacer" />
                    <b>{value}</b>
                  </div>
                ))}
              </div>
              <p className="muted">
                最久排队 {number(data.current.oldestQueueSeconds, ' s')}
                。资源经独立核验释放后才归还配额。
              </p>
            </section>
          </div>
        </>
      )}
      {platformAdmin && (
        <section className="panel resource-panel">
          <div className="panel-heading">
            <div>
              <h2>平台主机</h2>
              <p className="muted">
                平台管理员可见 · 共享主机整体资源，包含同机其他应用
              </p>
            </div>
            <span className="muted">
              {host.data?.sampledAt ? dateText(host.data.sampledAt) : ''}
            </span>
          </div>
          <ErrorBox message={host.error} retry={host.refresh} />
          {!host.data?.available ? (
            <div className="info-strip">
              {host.loading
                ? '正在读取主机指标…'
                : host.data?.reason === 'not_configured'
                  ? '尚未配置内部监控服务。'
                  : '指标源暂时不可用或采样已过期。'}
            </div>
          ) : (
            <>
              <div className="stats-grid">
                {[
                  ['CPU 使用率', 'cpuPercent', '%'],
                  ['内存使用率', 'memoryPercent', '%'],
                  ['根磁盘使用率', 'diskPercent', '%'],
                  ['1 分钟负载', 'load1', ''],
                ].map(([label, key, suffix]) => (
                  <div className="stat-card" key={key}>
                    <div>
                      <span>{label}</span>
                    </div>
                    <strong>{number(host.data?.values?.[key!], suffix)}</strong>
                  </div>
                ))}
              </div>
              <div className="status-chart">
                {[
                  ['平台 API', 'apiUp'],
                  ['指标采集', 'metricsHealthy'],
                  ['执行监督服务', 'supervisorUp'],
                  ['任务调度服务', 'temporalUp'],
                  ['展示入口', 'ingressUp'],
                ].map(([label, key]) => (
                  <div key={key}>
                    <span>{label}</span>
                    <span className="metric-spacer" />
                    <b>
                      {host.data?.values?.[key!] === 1
                        ? '正常'
                        : host.data?.values?.[key!] === 0
                          ? '异常'
                          : '未报告'}
                    </b>
                  </div>
                ))}
              </div>
            </>
          )}
        </section>
      )}
    </div>
  );
}
