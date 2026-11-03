export interface SkillSandboxConfig {
  provider: string;
  enabled: boolean;
  apiKey: string;
  apiKeyMasked?: boolean;
  timeoutSeconds: number;
  allowInternetAccess: boolean;
  lastTestStatus?: string;
  lastTestMessage?: string;
  lastTestTime?: string;
  spaceId?: number;
}

export interface WorkflowArtifact {
  id: number;
  workflowId: number;
  runId?: string;
  nodeId?: string;
  skillId?: string;
  fileName: string;
  contentType?: string;
  fileSize?: number;
  source?: string;
  downloadUrl?: string;
  createTime?: string;
}
