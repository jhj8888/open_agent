import http from '@/utils/http';
import type { Environment } from './platform-tenants';

export interface ModelConnection {
  connection_id: string;
  display_name: string;
  current_version: number;
  endpoint: string;
  model_name: string;
  protocol: 'openai';
  supports_stream: boolean;
  supports_tools: boolean;
  supports_usage: boolean;
  connect_timeout_seconds: number;
  read_timeout_seconds: number;
  total_timeout_seconds: number;
  ca_ref: string | null;
  credential_ref: string;
  revoked: boolean;
}
export interface ModelInput {
  displayName: string;
  endpoint: string;
  modelName: string;
  supportsStream: boolean;
  supportsTools: boolean;
  supportsUsage: boolean;
  connectTimeoutSeconds: number;
  readTimeoutSeconds: number;
  totalTimeoutSeconds: number;
  caRef?: string;
  apiKey?: string;
  expectedVersion: number;
  revokePrevious: boolean;
}
export interface ProbeResult {
  ok: boolean;
  version: number;
  durationMs: number;
  stream?: boolean;
  usage?: 'reported' | 'unknown';
  toolsVerified?: boolean;
  error?: string;
}
const path = (tenant: string, environment: Environment): string =>
  `/platform/tenants/${encodeURIComponent(tenant)}/environments/${environment}/models`;
export const listModels = async (
  tenant: string,
  environment: Environment
): Promise<ModelConnection[]> => await http.get(path(tenant, environment));
export const saveModel = async (
  tenant: string,
  environment: Environment,
  id: string | undefined,
  input: ModelInput
): Promise<void> => {
  if (id)
    await http.put(
      `${path(tenant, environment)}/${encodeURIComponent(id)}`,
      input
    );
  else await http.post(path(tenant, environment), input);
};
export const probeModel = async (
  tenant: string,
  environment: Environment,
  id: string
): Promise<ProbeResult> =>
  await http.post(
    `${path(tenant, environment)}/${encodeURIComponent(id)}/probe`,
    {},
    { timeout: 180000 }
  );
