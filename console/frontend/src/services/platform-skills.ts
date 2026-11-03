import http from '@/utils/http';
import type { Environment } from './platform-tenants';

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
export const importLegacySkill = async (
  tenant: string,
  environment: Environment,
  skillId: number,
  version: string,
  runtimeImage: string
): Promise<{ releaseId: string; manifestSha256: string; reused: boolean }> =>
  await http.post(
    `${path(tenant, environment)}/import-draft`,
    { skillId, version, runtimeImage },
    { timeout: 240000 }
  );
const path = (tenant: string, environment: Environment): string =>
  `/platform/tenants/${encodeURIComponent(tenant)}/environments/${environment}/skill-releases`;
export const listSkillReleases = async (
  tenant: string,
  environment: Environment
): Promise<SkillRelease[]> => await http.get(path(tenant, environment));
export const listSkillImages = async (
  tenant: string,
  environment: Environment
): Promise<string[]> =>
  await http.get(`${path(tenant, environment)}/runtime-images`);
export const publishSkillRelease = async (
  tenant: string,
  environment: Environment,
  archive: File,
  version: string,
  runtimeImage: string
): Promise<{ releaseId: string; manifestSha256: string; reused: boolean }> => {
  const body = new FormData();
  body.append('archive', archive);
  body.append('version', version);
  body.append('runtimeImage', runtimeImage);
  return await http.post(path(tenant, environment), body, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 240000,
  });
};
export const getSkillManifest = async (
  tenant: string,
  environment: Environment,
  release: string
): Promise<SkillManifest> => {
  const response = await http.get<Blob>(
    `${path(tenant, environment)}/${encodeURIComponent(release)}/manifest`,
    { responseType: 'blob' }
  );
  return JSON.parse(await response.data.text()) as SkillManifest;
};
export const getSkillFile = async (
  tenant: string,
  environment: Environment,
  release: string,
  index: number
): Promise<Blob> => {
  const response = await http.get<Blob>(
    `${path(tenant, environment)}/${encodeURIComponent(release)}/files/${index}`,
    { responseType: 'blob' }
  );
  return response.data;
};
export const promoteSkillRelease = async (
  tenant: string,
  release: string
): Promise<void> => {
  await http.post(
    `${path(tenant, 'prod')}/${encodeURIComponent(release)}/promote`,
    {}
  );
};
export const deleteSkillRelease = async (
  tenant: string,
  environment: Environment,
  release: string
): Promise<void> => {
  await http.delete(
    `${path(tenant, environment)}/${encodeURIComponent(release)}`
  );
};
