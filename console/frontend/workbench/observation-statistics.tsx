import { useState } from 'react';
import { ApiScope } from './api';
import { ErrorBox, dateText, useResource } from './components';

type Daily = {
  date: string;
  tasks: number;
  modelCalls: number;
  unknownModelCalls: number;
  inputTokens: number;
  outputTokens: number;
};
type Report = {
  period: string;
  timezone: string;
  start: string;
  end: string;
  asOf: string;
  tasks: {
    tasks: number;
    finishedTasks: number;
    succeededTasks: number;
    failedTasks: number;
    cancelledTasks: number;
    timedOutTasks: number;
    meanTaskMs: number | null;
    p95TaskMs: number | null;
  };
  usage: {
    modelCalls: number;
    unknownModelCalls: number;
    incompleteModelCalls: number;
    inputTokens: number;
    outputTokens: number;
    cacheReadTokens: number | null;
    cacheWriteTokens: number | null;
    meanModelMs: number | null;
    p95ModelMs: number | null;
  };
  daily: Daily[];
  tools: {
    name: string;
    calls: number;
    failedCalls: number;
    meanMs: number | null;
  }[];
};
const amount = (value: number | null | undefined) =>
  value == null
    ? '未报告'
    : Number(value).toLocaleString(undefined, { maximumFractionDigits: 1 });
const seconds = (value: number | null | undefined) =>
  value == null ? '未报告' : `${(Number(value) / 1000).toFixed(2)} s`;

export function ObservationStatistics({ api }: { api: ApiScope }): JSX.Element {
  const [period, setPeriod] = useState('this_week');
  const report = useResource(
    () => api.list<Report>(`observation-statistics?period=${period}`),
    [api, period]
  );
  const data = report.data?.period === period ? report.data : undefined;
  const download = (): void => {
    if (!data) return;
    // Only fixed headers, validated dates and numeric fields; no formula-capable user strings.
    const header = [
      '日期',
      '任务数',
      '模型调用数',
      '未知用量调用数',
      '输入Token',
      '输出Token',
      '时区',
      '统计截止',
    ];
    const rows = data.daily.map(row => [
      row.date,
      row.tasks,
      row.modelCalls,
      row.unknownModelCalls,
      row.inputTokens,
      row.outputTokens,
      data.timezone,
      data.asOf,
    ]);
    const text =
      '\uFEFF' +
      [header, ...rows]
        .map(row =>
          row.map(value => `"${String(value).replace(/"/g, '""')}"`).join(',')
        )
        .join('\r\n');
    const url = URL.createObjectURL(
      new Blob([text], { type: 'text/csv;charset=utf-8' })
    );
    const link = document.createElement('a');
    link.href = url;
    link.download = `agent-${period}-${data.start.slice(0, 10)}.csv`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  return (
    <section className="panel resource-panel" aria-label="Agent 周月统计">
      <div className="panel-heading">
        <div>
          <h2>Agent 周 / 月统计</h2>
          <p className="muted">
            自然周从周一开始，自然月从一日开始 · Asia/Shanghai
          </p>
        </div>
        <div className="top-actions">
          <select
            aria-label="Agent 统计周期"
            value={period}
            onChange={event => setPeriod(event.target.value)}
          >
            <option value="this_week">本周</option>
            <option value="last_week">上周</option>
            <option value="this_month">本月</option>
            <option value="last_month">上月</option>
          </select>
          <button className="button" onClick={report.refresh}>
            刷新
          </button>
          <button className="secondary" disabled={!data} onClick={download}>
            导出每日统计
          </button>
        </div>
      </div>
      <ErrorBox message={report.error} retry={report.refresh} />
      {report.loading && !data && (
        <div className="info-strip">正在汇总逐步执行数据…</div>
      )}
      {data && (
        <>
          <div className="info-strip">
            {dateText(data.start)} — {dateText(data.end)}（结束不含）· 截至{' '}
            {dateText(data.asOf)}
            。任务按创建时间，用量按模型调用结束时间统计；旧任务缺失步骤不补算。
          </div>
          <div className="stats-grid">
            {[
              [
                '周期内任务',
                amount(data.tasks.tasks),
                `成功 ${data.tasks.succeededTasks} · 失败 ${data.tasks.failedTasks} · 取消 ${data.tasks.cancelledTasks} · 超时 ${data.tasks.timedOutTasks}`,
              ],
              [
                '已报告 Token',
                amount(
                  Number(data.usage.inputTokens) +
                    Number(data.usage.outputTokens)
                ),
                `输入 ${amount(data.usage.inputTokens)} · 输出 ${amount(data.usage.outputTokens)}`,
              ],
              [
                '模型调用',
                amount(data.usage.modelCalls),
                `未知用量 ${data.usage.unknownModelCalls} · 未完整上报 ${data.usage.incompleteModelCalls}`,
              ],
              [
                '任务 P95 耗时',
                seconds(data.tasks.p95TaskMs),
                `平均 ${seconds(data.tasks.meanTaskMs)} · 墙钟耗时，不叠加并行步骤`,
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
          <p className="form-note">
            模型平均 {seconds(data.usage.meanModelMs)} · P95{' '}
            {seconds(data.usage.p95ModelMs)} · 缓存读取{' '}
            {amount(data.usage.cacheReadTokens)}{' '}
            Token（已包含在输入中）。未知用量不计为零；未结束调用不计入已结束调用总量。
          </p>
          <details className="period-details" open>
            <summary>每日任务与用量</summary>
            <div className="period-table">
              <table>
                <thead>
                  <tr>
                    <th>日期</th>
                    <th>任务</th>
                    <th>模型调用</th>
                    <th>输入 Token</th>
                    <th>输出 Token</th>
                    <th>未知用量</th>
                  </tr>
                </thead>
                <tbody>
                  {data.daily.map(row => (
                    <tr key={row.date}>
                      <td>{row.date}</td>
                      <td>{amount(row.tasks)}</td>
                      <td>{amount(row.modelCalls)}</td>
                      <td>{amount(row.inputTokens)}</td>
                      <td>{amount(row.outputTokens)}</td>
                      <td>{amount(row.unknownModelCalls)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
          <details className="period-details">
            <summary>工具调用统计 · {data.tools.length} 类</summary>
            <div className="period-table">
              <table>
                <thead>
                  <tr>
                    <th>工具</th>
                    <th>调用</th>
                    <th>失败 / 中断</th>
                    <th>平均耗时</th>
                  </tr>
                </thead>
                <tbody>
                  {data.tools.map(row => (
                    <tr key={row.name}>
                      <td>{row.name}</td>
                      <td>{amount(row.calls)}</td>
                      <td>{amount(row.failedCalls)}</td>
                      <td>{seconds(row.meanMs)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!data.tools.length && (
                <p className="form-note">该周期尚无已结束的工具调用。</p>
              )}
            </div>
          </details>
        </>
      )}
    </section>
  );
}
