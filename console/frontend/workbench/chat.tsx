import { useEffect, useRef, useState } from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { ApiScope, aborted, errorMessage } from './api';
import {
  dateText,
  ErrorBox,
  Icon,
  Status,
  terminal,
  useDialogFocus,
} from './components';
import type { AgentRun, Deployment, RunEvent } from './types';
import { TaskOutputs } from './outputs';
import { TaskObservations } from './observations';

export function RunConsole({
  api,
  deployment,
  initialRun,
  close,
  changed,
}: {
  api: ApiScope;
  deployment: Deployment;
  initialRun?: string;
  close: () => void;
  changed: () => void;
}): JSX.Element {
  const dialog = useDialogFocus<HTMLElement>(close);
  const [metadata, setMetadata] = useState<Deployment>();
  const [rows, setRows] = useState<AgentRun[]>([]);
  const [selected, setSelected] = useState(initialRun);
  const [run, setRun] = useState<AgentRun>();
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [input, setInput] = useState('');
  const [prompt, setPrompt] = useState('');
  const [history, setHistory] = useState<
    { role: 'user' | 'assistant'; content: string }[]
  >([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [connection, setConnection] = useState(0);
  const [streaming, setStreaming] = useState(false);
  const pending = useRef<{ text: string; key: string }>();
  const alive = useRef(true);
  const updated = useRef(changed);
  updated.current = changed;
  useEffect(() => {
    alive.current = true;
    void api
      .request<Deployment>(
        `/api/v1/agent-deployments/${deployment.deployment_id}`
      )
      .then(async value => {
        if (!alive.current) return;
        setMetadata(value);
        if (value.allowed_actions?.read) {
          const items = await api.request<AgentRun[]>(
            `/api/v1/agent-deployments/${deployment.deployment_id}/runs`
          );
          if (alive.current) setRows(items);
        }
      })
      .catch(failure => {
        if (alive.current && !aborted(failure)) setError(errorMessage(failure));
      });
    return () => {
      alive.current = false;
    };
  }, [api, deployment.deployment_id]);
  useEffect(() => {
    setRun(undefined);
    setEvents([]);
    setError('');
    setStreaming(false);
    if (!selected) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const refresh = async (): Promise<void> => {
      try {
        const value = await api.request<AgentRun>(
          `/api/v1/runs/${selected}`,
          'GET',
          undefined,
          { signal: controller.signal }
        );
        if (controller.signal.aborted) return;
        setRun(value);
        setRows(previous =>
          [
            value,
            ...previous.filter(item => item.run_id !== value.run_id),
          ].slice(0, 50)
        );
        if (terminal(value.state)) updated.current();
        else
          timer = setTimeout(() => {
            void refresh();
          }, 2500);
      } catch (failure) {
        if (!controller.signal.aborted) setError(errorMessage(failure));
      }
    };
    void refresh();
    setStreaming(true);
    void api
      .stream(
        selected,
        controller.signal,
        event => {
          if (!controller.signal.aborted)
            setEvents(previous => [...previous, event]);
        },
        () => {
          if (controller.signal.aborted) return;
          setStreaming(false);
          if (timer) clearTimeout(timer);
          void refresh();
        }
      )
      .catch(failure => {
        if (!controller.signal.aborted) {
          setError(errorMessage(failure));
          setStreaming(false);
        }
      });
    return () => {
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, [api, selected, connection]);
  const output = events
    .filter(event => event.event_type === 'message.delta')
    .map(event => event.payload.text ?? '')
    .join('');
  const active = !!run && !terminal(run.state);
  const send = async (): Promise<void> => {
    if (busy || !input.trim()) return;
    const text = input.trim();
    if (pending.current?.text !== text)
      pending.current = { text, key: crypto.randomUUID() };
    const conversation =
      run?.state === 'succeeded' && prompt && output
        ? [
            ...history,
            { role: 'user' as const, content: prompt },
            { role: 'assistant' as const, content: output },
          ]
        : history;
    setBusy(true);
    setError('');
    try {
      const result = await api.request<{ run_id: string }>(
        `/api/v1/agent-deployments/${deployment.deployment_id}/runs`,
        'POST',
        {
          input: { message: text, history: conversation.slice(-20) },
          timeoutSeconds: 300,
        },
        { headers: { 'Idempotency-Key': pending.current.key } }
      );
      if (!alive.current) return;
      setSelected(result.run_id);
      setPrompt(text);
      setHistory(conversation.slice(-20));
      setInput('');
      pending.current = undefined;
      updated.current();
    } catch (failure) {
      if (alive.current && !aborted(failure))
        setError(
          `${errorMessage(failure)} 提交未确认时，原消息重试会沿用同一请求键。`
        );
    } finally {
      if (alive.current) setBusy(false);
    }
  };
  const cancel = async (): Promise<void> => {
    if (!selected || busy) return;
    setBusy(true);
    try {
      const result = await api.request<AgentRun>(
        `/api/v1/runs/${selected}/cancel`,
        'POST'
      );
      if (alive.current) setRun(result);
    } catch (failure) {
      if (alive.current && !aborted(failure)) setError(errorMessage(failure));
    } finally {
      if (alive.current) setBusy(false);
    }
  };
  return (
    <div className="chat-backdrop">
      <section
        ref={dialog}
        className="chat-drawer live-chat"
        role="dialog"
        aria-modal="true"
        aria-label="聊天调试"
      >
        <header className="drawer-heading">
          <span className="agent-icon green">
            <Icon name="spark" />
          </span>
          <div>
            <h2>{deployment.display_name}</h2>
            <p>
              {deployment.agent_version} · DeepAgents ·{' '}
              {api.environment === 'test' ? '测试环境' : '生产环境'}
            </p>
          </div>
          <button className="icon-button" aria-label="关闭聊天" onClick={close}>
            <Icon name="close" />
          </button>
        </header>
        <div className="chat-toolbar">
          <select
            aria-label="任务历史"
            value={selected ?? ''}
            onChange={event => {
              setSelected(event.target.value || undefined);
              setPrompt('');
              setHistory([]);
              pending.current = undefined;
            }}
          >
            <option value="">新建任务</option>
            {rows.map(item => (
              <option key={item.run_id} value={item.run_id}>
                {item.run_id.slice(0, 8)} · {item.state} ·{' '}
                {dateText(item.created_at)}
              </option>
            ))}
          </select>
          <button
            className="link"
            onClick={() => {
              setSelected(undefined);
              setPrompt('');
              setInput('');
              setHistory([]);
              pending.current = undefined;
            }}
          >
            新建任务
          </button>
        </div>
        <ErrorBox
          message={error}
          retry={() => setConnection(value => value + 1)}
        />
        {metadata && !metadata.admission_enabled && (
          <div className="inline-note">
            执行服务暂未就绪，当前可查看历史任务。
          </div>
        )}
        <div className="chat-scroll">
          {!selected ? (
            <div className="chat-welcome">
              <Icon name="spark" size={36} />
              <h2>开始一次真实任务</h2>
              <p>
                消息将提交到当前部署，执行过程和结果实时更新。
                <br />
                关闭窗口不会中断任务，可从任务中心继续查看。
              </p>
              <div className="suggestions">
                {[
                  '介绍你能执行的任务。',
                  '列出可用 Skill，并说明使用方式。',
                ].map(text => (
                  <button key={text} onClick={() => setInput(text)}>
                    {text}
                    <Icon name="arrow" size={14} />
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <>
              {prompt && (
                <div className="chat-message user-message">
                  <span className="message-avatar">我</span>
                  <div>{prompt}</div>
                </div>
              )}
              <div className="chat-message assistant-message">
                <span className="message-avatar agent">
                  <Icon name="spark" size={17} />
                </span>
                <div>
                  <div className="message-label">
                    {deployment.display_name}
                    <span>真实模型响应</span>
                  </div>
                  <div className="answer-text answer-markdown">
                    {output ? (
                      <Markdown
                        remarkPlugins={[remarkGfm]}
                        skipHtml
                        components={{
                          img: ({ alt }) => (
                            <span>[图片：{alt || '未加载'}]</span>
                          ),
                          a: ({ href, children }) => (
                            <a
                              href={href}
                              rel="noreferrer noopener"
                              target="_blank"
                            >
                              {children}
                            </a>
                          ),
                        }}
                      >
                        {output}
                      </Markdown>
                    ) : active || streaming ? (
                      '正在等待执行结果…'
                    ) : (
                      '本次任务没有文本输出。'
                    )}
                  </div>
                  {run && <TaskOutputs key={run.run_id} api={api} run={run} />}
                  {run && <TaskObservations key={`trace-${run.run_id}`} api={api} run={run} />}
                  <details className="execution-details">
                    <summary>
                      <Icon name="terminal" size={14} />
                      执行记录 · {events.length} 条
                      {run && <Status state={run.state} />}
                    </summary>
                    <div className="event-log">
                      {events
                        .filter(event => event.event_type !== 'message.delta')
                        .map(event => (
                          <div key={event.sequence_no}>
                            <b>
                              #{event.sequence_no} {event.event_type}
                            </b>
                            <pre>{JSON.stringify(event.payload, null, 2)}</pre>
                          </div>
                        ))}
                    </div>
                  </details>
                  {run && (
                    <div className="task-meta">
                      <span>{run.run_id}</span>
                      <span>Token：{run.usage?.total_tokens ?? '未报告'}</span>
                      <button
                        className="link"
                        onClick={() => setConnection(value => value + 1)}
                      >
                        重新连接
                      </button>
                    </div>
                  )}
                  {run?.error_code && (
                    <ErrorBox message={`任务结束：${run.error_code}`} />
                  )}
                  <p className="form-note">
                    追踪 ID：{run?.trace_id ?? '读取中'}
                    {!prompt && ' · 历史视图展示已保存的输出与执行记录。'}
                  </p>
                </div>
              </div>
            </>
          )}
        </div>
        <form
          className="chat-composer"
          onSubmit={event => {
            event.preventDefault();
            void send();
          }}
        >
          <textarea
            aria-label="调试消息"
            placeholder="输入任务，Shift + Enter 换行"
            value={input}
            maxLength={20000}
            onChange={event => setInput(event.target.value)}
            onKeyDown={event => {
              if (
                event.key === 'Enter' &&
                !event.shiftKey &&
                !event.nativeEvent.isComposing
              ) {
                event.preventDefault();
                if (!active) void send();
              }
            }}
          />
          <div>
            <span>
              {active
                ? '任务执行中，可取消或另建任务'
                : '每次发送创建可追踪的独立任务'}
            </span>
            {active ? (
              <button
                className="button"
                type="button"
                disabled={busy || !metadata?.allowed_actions?.cancel}
                onClick={() => {
                  void cancel();
                }}
              >
                取消任务
              </button>
            ) : (
              <button
                className="button primary"
                disabled={
                  busy ||
                  !input.trim() ||
                  !metadata?.admission_enabled ||
                  !metadata.allowed_actions?.create
                }
              >
                <Icon name="send" size={15} />
                {busy ? '提交中' : '发送'}
              </button>
            )}
          </div>
        </form>
      </section>
    </div>
  );
}
