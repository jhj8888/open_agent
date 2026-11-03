/** ToolBoxExportVo returned from an import before the draft is persisted. */
export interface ToolImportData {
  name?: string | null;
  description?: string | null;
  icon?: string | null;
  address?: string | null;
  endPoint?: string | null;
  method?: string | null;
  webSchema?: string | null;
  authInfo?: string | null;
  avatarColor?: string | null;
  authType?: number | null;
}
