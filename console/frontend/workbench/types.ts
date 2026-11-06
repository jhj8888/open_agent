export type Environment = 'test' | 'prod';
export type TenantRole = 'tenant_admin' | 'developer' | 'operator' | 'viewer';
export interface Tenant {
  tenantId: string;
  displayName: string;
  scopeKind: 'space' | 'personal';
  status: 'active' | 'suspended' | 'archived';
  revision: number;
  spaceId: number | null;
}
export interface Membership {
  environment: Environment;
  role: TenantRole;
  revision: number;
}
export interface Member {
  uid: string;
  username: string;
  role: TenantRole;
  revision: number;
}
export interface Quotas {
  maxConcurrentRuns: number;
  maxQueuedRuns: number;
  maxSandboxes: number;
  dailyTokenBudget: number;
  auditRetentionDays: number;
  revision: number;
}
export interface AuditEntry {
  id: number;
  actorUid: string;
  action: string;
  subjectId: string;
  beforeValue: string | null;
  afterValue: string | null;
  createdAt: string;
}

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
  allowed_actions?: {
    create: boolean;
    read: boolean;
    cancel: boolean;
    delete?: boolean;
  };
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

export interface ModelConnection {
  connection_id: string;
  display_name: string;
  current_version: number;
  endpoint: string;
  model_name: string;
  protocol: 'openai';
  adapter: 'openai' | 'deepseek';
  thinking_enabled: boolean;
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
  adapter: 'openai' | 'deepseek';
  thinkingEnabled: boolean;
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

export interface SkillRelease {
  release_id: string;
  skill_name: string;
  description: string;
  version_label: string;
  manifest_sha256: string;
  runtime_image: string;
  total_bytes: number;
  file_count: number;
  status: 'ready' | 'revoked' | 'deleted';
  source_kind: 'upload' | 'legacy-import';
  created_at: string;
  retain_until: string;
  verified: boolean | number;
}
export interface SkillManifest {
  schemaVersion: number;
  name: string;
  version: string;
  runtimeImage: string;
  dependencyPolicy: string;
  files: {
    path: string;
    size: number;
    sha256: string;
    dependencyLock: boolean;
  }[];
}
