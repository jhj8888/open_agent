import React from 'react';
import { useEffect, useRef, useState } from 'react';
import { aborted, errorMessage } from './api';
import type { RunState } from './types';
const paths: Record<string, React.ReactNode> = {
  grid: (
    <>
      <rect x="3" y="3" width="7" height="7" rx="2" />
      <rect x="14" y="3" width="7" height="7" rx="2" />
      <rect x="3" y="14" width="7" height="7" rx="2" />
      <rect x="14" y="14" width="7" height="7" rx="2" />
    </>
  ),
  chart: (
    <>
      <path d="M4 4v16h17M8 15v-4m5 4V7m5 8v-6" />
      <path d="m7 7 5-3 6 2" />
    </>
  ),
  document: (
    <>
      <path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9zM14 3v6h6M8 13h8M8 17h5" />
    </>
  ),
  code: (
    <>
      <path d="m8 7-5 5 5 5m8-10 5 5-5 5m-3-13-2 16" />
    </>
  ),
  spark: (
    <>
      <path d="m12 3 2.7 6.3L21 12l-6.3 2.7L12 21l-2.7-6.3L3 12l6.3-2.7z" />
    </>
  ),
  terminal: (
    <>
      <rect x="3" y="4" width="18" height="16" rx="3" />
      <path d="m7 9 3 3-3 3m6 0h4" />
    </>
  ),
  shield: (
    <>
      <path d="m12 3 8 3v6c0 4-4 7-8 9-4-2-8-5-8-9V6zM8 12l3 3 5-6" />
    </>
  ),
  box: (
    <>
      <path d="m12 3 9 5-9 5-9-5zM3 8v9l9 5 9-5V8M12 13v9M8 5l9 5" />
    </>
  ),
  tasks: (
    <>
      <path d="m3 6 2 2 3-4m3 2h10M3 13l2 2 3-4m3 2h10M4 20h3m4 0h10" />
    </>
  ),
  people: (
    <>
      <circle cx="9" cy="7" r="3" />
      <path d="M3 21v-3a6 6 0 0 1 12 0v3M16 4a3 3 0 0 1 0 6m2 5a5 5 0 0 1 3 4v2" />
    </>
  ),
  pulse: <path d="M2 12h4l3-8 5 16 3-8h5" />,
  api: (
    <>
      <path d="m8 5-6 7 6 7m8-14 6 7-6 7M14 3l-4 18" />
    </>
  ),
  plus: <path d="M12 5v14M5 12h14" />,
  search: (
    <>
      <circle cx="10.5" cy="10.5" r="6.5" />
      <path d="m16 16 5 5" />
    </>
  ),
  chevron: <path d="m9 5 7 7-7 7" />,
  down: <path d="m6 9 6 6 6-6" />,
  arrow: <path d="M4 12h16m-6-6 6 6-6 6" />,
  send: (
    <>
      <path d="m3 3 19 9-19 9 3-9zM6 12h16" />
    </>
  ),
  close: <path d="m6 6 12 12M6 18 18 6" />,
  clock: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </>
  ),
  check: <path d="m5 12 4 4L19 6" />,
  copy: (
    <>
      <rect x="8" y="8" width="12" height="13" rx="2" />
      <path d="M16 8V3H3v13h5" />
    </>
  ),
  settings: (
    <>
      <path d="M4 7h16M4 17h16" />
      <circle cx="9" cy="7" r="3" fill="currentColor" />
      <circle cx="16" cy="17" r="3" fill="currentColor" />
    </>
  ),
  more: (
    <>
      <circle cx="5" cy="12" r="1" />
      <circle cx="12" cy="12" r="1" />
      <circle cx="19" cy="12" r="1" />
    </>
  ),
  refresh: (
    <>
      <path d="M20 10a8 8 0 1 0-2 8M20 4v6h-6" />
    </>
  ),
  play: <path d="m8 4 13 8-13 8z" />,
};
export function Icon({
  name,
  size = 20,
}: {
  name: string;
  size?: number;
}): JSX.Element {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {paths[name] ?? paths.spark}
    </svg>
  );
}

export const stateNames: Record<string, string> = {
  queued: '排队中',
  running: '运行中',
  cancel_requested: '正在取消',
  cancelled: '已取消',
  succeeded: '已完成',
  failed: '失败',
  timed_out: '超时',
  test: '测试部署',
  published: '已发布',
  disabled: '已停用',
  ready: '可用',
  revoked: '已撤销',
  active: '正常',
  suspended: '已暂停',
  archived: '已归档',
};
export const terminal = (state: RunState): boolean =>
  ['succeeded', 'failed', 'timed_out', 'cancelled'].includes(state);
export const dateText = (value?: string | null): string => {
  if (!value) return '—';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toLocaleString('zh-CN', { hour12: false });
};
export function Status({ state }: { state: string }): JSX.Element {
  return (
    <span className={`status ${state}`}>
      <i />
      {stateNames[state] ?? state}
    </span>
  );
}
export function Empty({
  text = '暂无记录',
  detail = '当前范围内还没有可见数据。',
}: {
  text?: string;
  detail?: string;
}): JSX.Element {
  return (
    <div className="empty">
      <Icon name="box" size={32} />
      <h3>{text}</h3>
      <p>{detail}</p>
    </div>
  );
}
export function ErrorBox({
  message,
  retry,
}: {
  message?: string;
  retry?: () => void;
}): JSX.Element | null {
  return message ? (
    <div className="error-box" role="alert">
      <span>{message}</span>
      {retry && (
        <button className="link" onClick={retry}>
          重试
        </button>
      )}
    </div>
  ) : null;
}
export function useDialogFocus<T extends HTMLElement>(
  onClose: () => void
): React.RefObject<T> {
  const ref = useRef<T>(null);
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    const prior = document.activeElement as HTMLElement | null;
    const node = ref.current;
    const focusables = (): HTMLElement[] =>
      Array.from(
        node?.querySelectorAll<HTMLElement>(
          'button:not(:disabled),input:not(:disabled),textarea:not(:disabled),select:not(:disabled),a[href],summary,[tabindex="0"]'
        ) ?? []
      ).filter(target => target.getClientRects().length > 0);
    focusables()[0]?.focus();
    const handler = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') close.current();
      if (event.key === 'Tab') {
        const targets = focusables();
        const first = targets[0];
        const last = targets[targets.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last?.focus();
        }
        if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first?.focus();
        }
      }
    };
    node?.addEventListener('keydown', handler);
    return () => {
      node?.removeEventListener('keydown', handler);
      prior?.focus();
    };
  }, []);
  return ref;
}
export function Modal({
  title,
  onClose,
  children,
  wide = false,
}: React.PropsWithChildren<{
  title: string;
  onClose: () => void;
  wide?: boolean;
}>): JSX.Element {
  const ref = useDialogFocus<HTMLDivElement>(onClose);
  return (
    <div className="modal-backdrop">
      <div
        ref={ref}
        className={`modal ${wide ? 'wide' : ''}`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <header>
          <div>
            <div className="eyebrow">OPEN AGENT</div>
            <h2>{title}</h2>
          </div>
          <button className="icon-button" aria-label="关闭" onClick={onClose}>
            <Icon name="close" />
          </button>
        </header>
        {children}
      </div>
    </div>
  );
}
export function useResource<T>(
  load: () => Promise<T>,
  dependencies: React.DependencyList
): { data?: T; error: string; loading: boolean; refresh: () => void } {
  const [data, setData] = useState<T>();
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let current = true;
    setLoading(true);
    setError('');
    void load()
      .then(value => {
        if (current) setData(value);
      })
      .catch(failure => {
        if (current && !aborted(failure)) {
          setError(errorMessage(failure));
          setData(undefined);
        }
      })
      .finally(() => {
        if (current) setLoading(false);
      });
    return () => {
      current = false;
    };
    // The caller supplies its complete resource identity, like React's effect API.
  }, [...dependencies, revision]);
  return {
    data,
    error,
    loading,
    refresh: () => setRevision(value => value + 1),
  };
}
