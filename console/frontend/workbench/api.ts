import { fetchEventSource } from '@microsoft/fetch-event-source';
import { accessToken } from './auth';
import type { Environment, RunEvent } from './types';

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string
  ) {
    const reason: Record<number, string> = {
      400: '请检查输入内容',
      401: '登录已过期，请重新登录',
      403: '当前账号没有此操作权限',
      404: '记录不存在或已不可访问',
      409: '版本、配额或发布条件不满足，请刷新后检查',
      413: '上传文件超过服务限制',
      422: '输入或依赖配置不符合要求',
      429: '请求过于频繁或配额已用尽',
      503: '服务暂时不可用，请稍后重试',
    };
    super(
      `${reason[status] ?? '操作未完成，请稍后重试'}${code ? `（${code}）` : ''}。`
    );
  }
}
export const errorMessage = (error: unknown): string =>
  error instanceof Error ? error.message : '操作未完成。';
export const aborted = (error: unknown): boolean =>
  error instanceof DOMException && error.name === 'AbortError';

// One instance per tenant/environment. Unmounting that scope aborts all its requests.
export class ApiScope {
  readonly controller = new AbortController();
  readonly root: string;
  constructor(
    readonly tenant: string,
    readonly environment: Environment
  ) {
    this.root = `/platform/tenants/${encodeURIComponent(tenant)}/environments/${environment}`;
  }
  dispose(): void {
    this.controller.abort();
  }
  async request<T>(
    path: string,
    method = 'GET',
    body?: unknown,
    options: RequestInit = {}
  ): Promise<T> {
    if (!/^\/(platform\/|api\/v1\/)/.test(path))
      throw new Error('无效的接口路径。');
    const token = await accessToken();
    const headers = new Headers(options.headers);
    headers.set('Authorization', `Bearer ${token}`);
    headers.set('Accept', 'application/json');
    if (body !== undefined && !(body instanceof FormData))
      headers.set('Content-Type', 'application/json');
    const response = await fetch(path, {
      ...options,
      method,
      headers,
      credentials: 'omit',
      cache: 'no-store',
      body:
        body instanceof FormData
          ? body
          : body === undefined
            ? undefined
            : JSON.stringify(body),
      signal: options.signal
        ? AbortSignal.any([this.controller.signal, options.signal])
        : this.controller.signal,
    });
    if (response.status === 204) return undefined as T;
    const result = (await response.json().catch(() => null)) as {
      code?: number | string;
      data?: T;
    } | null;
    if (!response.ok || result?.code !== 0) {
      const code = String(result?.code ?? '');
      throw new ApiError(
        response.ok ? 400 : response.status,
        /^[a-zA-Z0-9_.-]{1,70}$/.test(code) ? code : ''
      );
    }
    return result.data as T;
  }
  list<T>(resource: string): Promise<T> {
    return this.request<T>(`${this.root}/${resource}`);
  }
  mutate<T>(resource: string, body?: unknown, method = 'POST'): Promise<T> {
    return this.request<T>(`${this.root}/${resource}`, method, body);
  }
  async file(resource: string, signal?: AbortSignal): Promise<string> {
    const response = await fetch(`${this.root}/${resource}`, {
      headers: { Authorization: `Bearer ${await accessToken()}` },
      credentials: 'omit',
      cache: 'no-store',
      signal: signal
        ? AbortSignal.any([signal, this.controller.signal])
        : this.controller.signal,
    });
    if (!response.ok) throw new ApiError(response.status, '');
    return response.text();
  }
  async downloadOutput(run: string, id: string, name: string): Promise<void> {
    const response = await fetch(`/api/v1/runs/${encodeURIComponent(run)}/outputs/${encodeURIComponent(id)}/download`, {
      headers: { Authorization: `Bearer ${await accessToken()}` },
      credentials: 'omit', cache: 'no-store', signal: this.controller.signal,
    });
    if (!response.ok) throw new ApiError(response.status, '');
    const blob = await response.blob();
    const expected = response.headers.get('X-Content-SHA256');
    const digest = await crypto.subtle.digest('SHA-256', await blob.arrayBuffer());
    const actual = Array.from(new Uint8Array(digest)).map(b => b.toString(16).padStart(2, '0')).join('');
    if (!expected || actual !== expected) throw new Error('文件完整性校验失败，请重新下载。');
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url; link.download = name; link.rel = 'noopener';
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async stream(
    id: string,
    signal: AbortSignal,
    receive: (event: RunEvent) => void,
    ended: () => void
  ): Promise<void> {
    let terminal = false;
    let failures = 0;
    let sequence = 0;
    class Stop extends Error {}
    await fetchEventSource(`/api/v1/runs/${encodeURIComponent(id)}/events`, {
      signal: AbortSignal.any([signal, this.controller.signal]),
      openWhenHidden: true,
      headers: { Accept: 'text/event-stream' },
      fetch: async (url, init) => {
        const headers = new Headers(init?.headers);
        headers.set('Authorization', `Bearer ${await accessToken()}`);
        return fetch(url, {
          ...init,
          headers,
          credentials: 'omit',
          cache: 'no-store',
        });
      },
      async onopen(response) {
        if (
          response.ok &&
          response.headers.get('content-type')?.includes('text/event-stream')
        ) {
          failures = 0;
          return;
        }
        if ([400, 401, 403, 404, 410, 422].includes(response.status))
          throw new Stop(new ApiError(response.status, '').message);
        throw new Error('事件连接暂时中断');
      },
      onmessage(event) {
        if (!event.data) return;
        const value = JSON.parse(event.data) as RunEvent & {
          terminal?: boolean;
          code?: string;
        };
        if (value.run_id !== id) throw new Stop('任务事件不匹配。');
        if (event.event === 'stream.end') {
          terminal = true;
          ended();
          return;
        }
        if (event.event === 'error') {
          if (!value.terminal)
            throw new Stop('事件订阅不可用，请刷新权限后重连。');
          return;
        }
        if (
          !Number.isSafeInteger(value.sequence_no) ||
          value.sequence_no <= sequence
        )
          return;
        sequence = value.sequence_no;
        receive(value);
      },
      onclose() {
        if (!terminal) throw new Error('事件连接已断开');
      },
      onerror(error: unknown) {
        if (error instanceof Stop) throw error;
        if (++failures > 10)
          throw new Stop('连接暂不可用。任务继续执行，可重新连接查看。');
        return Math.min(10000, failures * 1000);
      },
    });
  }
}
