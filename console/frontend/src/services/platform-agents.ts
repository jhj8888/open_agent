import http, { baseURL } from '@/utils/http';
import { fetchEventSource } from '@microsoft/fetch-event-source';
import type { Environment } from './platform-tenants';

export interface Deployment {
  deployment_id: string;
  display_name: string;
  environment: Environment;
  agent_version: string;
  executor_kind: string;
  model_connection_id: string;
  model_version: number;
  snapshot_sha256: string;
  state: 'test' | 'published' | 'disabled';
  created_at: string;
  admission_enabled?: boolean;
  allowed_actions?: { create: boolean; read: boolean; cancel: boolean };
}
export interface AgentInput {
  displayName: string;
  agentVersion: string;
  modelConnectionId: string;
  modelVersion: number;
  skillReleaseIds: string[];
  execution: {
    instructions: string;
    maxLoops?: number;
    temperature?: number;
    maxTokens?: number;
  };
}
export type RunState =
  | 'queued'
  | 'running'
  | 'cancel_requested'
  | 'cancelled'
  | 'succeeded'
  | 'failed'
  | 'timed_out';
export interface AgentRun {
  run_id: string;
  deployment_id?: string;
  state: RunState;
  agent_version?: string;
  environment?: Environment;
  trace_id: string;
  error_code: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  usage?: {
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
  } | null;
}
export interface RunEvent {
  run_id: string;
  sequence_no: number;
  event_type: string;
  payload: {
    text?: string;
    state?: RunState;
    name?: string;
    phase?: string;
    total_tokens?: number;
  };
}
export interface DeploymentKey {
  key_id: string;
  display_name: string;
  can_create: boolean;
  can_read: boolean;
  can_cancel: boolean;
  revoked: boolean;
  expires_at: string;
}
const root = (tenant: string, environment: Environment): string =>
  `/platform/tenants/${encodeURIComponent(tenant)}/environments/${environment}/agent-deployments`;
const deploymentPath = (id: string): string =>
  `/api/v1/agent-deployments/${encodeURIComponent(id)}`;
const runPath = (id: string): string =>
  `/api/v1/runs/${encodeURIComponent(id)}`;
export const listDeployments = async (
  tenant: string,
  environment: Environment
): Promise<Deployment[]> => await http.get(root(tenant, environment));
export const createDeployment = async (
  tenant: string,
  input: AgentInput
): Promise<Deployment> => await http.post(root(tenant, 'test'), input);
export const promoteDeployment = async (
  tenant: string,
  sourceDeploymentId: string,
  verificationRunId: string,
  modelConnectionId: string,
  modelVersion: number
): Promise<Deployment> =>
  await http.post(`${root(tenant, 'prod')}/promote`, {
    sourceDeploymentId,
    verificationRunId,
    modelConnectionId,
    modelVersion,
  });
export const disableDeployment = async (
  tenant: string,
  environment: Environment,
  id: string
): Promise<void> =>
  await http.post(
    `${root(tenant, environment)}/${encodeURIComponent(id)}/disable`
  );
export const getDeployment = async (id: string): Promise<Deployment> =>
  await http.get(deploymentPath(id));
export const listRuns = async (id: string): Promise<AgentRun[]> =>
  await http.get(`${deploymentPath(id)}/runs`);
export const getRun = async (id: string): Promise<AgentRun> =>
  await http.get(runPath(id));
export const cancelRun = async (id: string): Promise<AgentRun> =>
  await http.post(`${runPath(id)}/cancel`);
export const submitRun = async (
  id: string,
  message: string,
  key: string,
  history: { role: 'user' | 'assistant'; content: string }[] = []
): Promise<{ run_id: string }> =>
  await http.post(
    `${deploymentPath(id)}/runs`,
    { input: { message, history }, timeoutSeconds: 300 },
    { headers: { 'Idempotency-Key': key } }
  );
export const listDeploymentKeys = async (
  tenant: string,
  environment: Environment,
  id: string
): Promise<DeploymentKey[]> =>
  await http.get(
    `${root(tenant, environment)}/${encodeURIComponent(id)}/credentials`
  );
export const issueDeploymentKey = async (
  tenant: string,
  environment: Environment,
  id: string,
  displayName: string
): Promise<{ keyId: string; token: string }> =>
  await http.post(
    `${root(tenant, environment)}/${encodeURIComponent(id)}/credentials`,
    {
      displayName,
      canCreate: true,
      canRead: true,
      canCancel: true,
      validDays: 30,
    }
  );
export const rotateDeploymentKey = async (
  tenant: string,
  environment: Environment,
  id: string,
  keyId: string
): Promise<{ keyId: string; token: string }> =>
  await http.post(
    `${root(tenant, environment)}/${encodeURIComponent(id)}/credentials/${encodeURIComponent(keyId)}/rotate`
  );
export const revokeDeploymentKey = async (
  tenant: string,
  environment: Environment,
  id: string,
  keyId: string
): Promise<void> =>
  await http.delete(
    `${root(tenant, environment)}/${encodeURIComponent(id)}/credentials/${encodeURIComponent(keyId)}`
  );
export const listDeploymentGrants = async (
  tenant: string,
  id: string
): Promise<{ uid: string }[]> =>
  await http.get(`${root(tenant, 'prod')}/${encodeURIComponent(id)}/grants`);
export const setDeploymentGrant = async (
  tenant: string,
  id: string,
  uid: string,
  enabled: boolean
): Promise<void> => {
  const url = `${root(tenant, 'prod')}/${encodeURIComponent(id)}/grants/${encodeURIComponent(uid)}`;
  if (enabled) await http.put(url);
  else await http.delete(url);
};
export const agentApiURL = (id: string): string =>
  new URL(
    `${baseURL.replace(/\/$/, '')}${deploymentPath(id)}/runs`,
    window.location.origin
  ).toString();

class StreamStopped extends Error {}
export async function subscribeRun(
  id: string,
  signal: AbortSignal,
  receive: (event: RunEvent) => void,
  ended: () => void
): Promise<void> {
  let terminal = false;
  let failures = 0;
  await fetchEventSource(`${baseURL.replace(/\/$/, '')}${runPath(id)}/events`, {
    signal,
    openWhenHidden: true,
    headers: {
      Authorization: `Bearer ${localStorage.getItem('accessToken') ?? ''}`,
      Accept: 'text/event-stream',
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
        throw new StreamStopped(
          `事件订阅不可用（${response.status}），请刷新权限后重试。`
        );
      throw new Error('事件连接暂时中断');
    },
    onmessage(event) {
      if (!event.data) return;
      const value = JSON.parse(event.data) as RunEvent & {
        code?: string;
        terminal?: boolean;
      };
      if (value.run_id !== id) throw new StreamStopped('任务事件不匹配');
      if (event.event === 'stream.end') {
        terminal = true;
        ended();
        return;
      }
      if (event.event === 'error') {
        if (value.terminal) return;
        throw new StreamStopped(
          `事件订阅已停止：${value.code ?? 'unavailable'}`
        );
      }
      receive(value);
    },
    onclose() {
      if (!terminal) throw new Error('事件连接已断开');
    },
    onerror(error: unknown) {
      if (error instanceof StreamStopped) throw error;
      if (++failures > 10)
        throw new StreamStopped('暂时无法连接。任务仍会继续，可重新连接查看。');
      return Math.min(10000, failures * 1000);
    },
  });
}
