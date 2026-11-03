import http from '@/utils/http';

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
const root = '/platform/tenants';
const tenantPath = (id: string): string => `${root}/${encodeURIComponent(id)}`;
const environmentPath = (id: string, environment: Environment): string =>
  `${tenantPath(id)}/environments/${environment}`;

export const listTenants = async (): Promise<{
  platformAdmin: boolean;
  tenants: Tenant[];
}> => await http.get(root);
export const createTenant = async (
  displayName: string,
  ownerUid: string
): Promise<{ tenantId: string }> =>
  await http.post(root, { displayName, ownerUid });
export const listMemberships = async (id: string): Promise<Membership[]> =>
  await http.get(`${tenantPath(id)}/memberships`);
export const setTenantStatus = async (
  tenant: Tenant,
  status: Tenant['status']
): Promise<void> =>
  await http.put(`${tenantPath(tenant.tenantId)}/status`, {
    status,
    revision: tenant.revision,
  });
export const getQuotas = async (
  id: string,
  environment: Environment
): Promise<Quotas> =>
  await http.get(`${environmentPath(id, environment)}/quotas`);
export const saveQuotas = async (
  id: string,
  environment: Environment,
  quotas: Quotas
): Promise<void> =>
  await http.put(`${environmentPath(id, environment)}/quotas`, quotas);
export const listMembers = async (
  id: string,
  environment: Environment
): Promise<Member[]> =>
  await http.get(`${environmentPath(id, environment)}/members`);
export const setMember = async (
  id: string,
  environment: Environment,
  uid: string,
  role: TenantRole,
  revision: number | null,
  bootstrap = false
): Promise<void> =>
  await http.put(
    `${environmentPath(id, environment)}/${bootstrap ? 'administrators' : 'members'}/${encodeURIComponent(uid)}`,
    { role, revision }
  );
export const removeMember = async (
  id: string,
  environment: Environment,
  member: Member
): Promise<void> =>
  await http.delete(
    `${environmentPath(id, environment)}/members/${encodeURIComponent(member.uid)}`,
    { params: { revision: member.revision } }
  );
export const listAudit = async (
  id: string,
  environment: Environment,
  beforeId?: number
): Promise<AuditEntry[]> =>
  await http.get(`${environmentPath(id, environment)}/audit`, {
    params: beforeId ? { beforeId } : {},
  });
