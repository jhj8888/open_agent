import type { ToolImportData } from '@/services/plugin';
import type { ToolItem } from '@/types/resource';

/** Import/export files describe a draft; persisted IDs and status are absent. */
export function importedToolDraft(data: ToolImportData): Partial<ToolItem> {
  return {
    name: data.name ?? undefined,
    description: data.description ?? undefined,
    icon: data.icon ?? undefined,
    address: data.address ?? undefined,
    endPoint: data.endPoint ?? undefined,
    method: data.method ?? undefined,
    webSchema: data.webSchema ?? undefined,
    authType: data.authType ?? undefined,
    authInfo: data.authInfo ?? undefined,
    avatarColor: data.avatarColor ?? undefined,
  };
}
