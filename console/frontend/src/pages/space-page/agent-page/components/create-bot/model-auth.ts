export interface ModelVersion {
  domain: string;
  status: number;
  name: string;
  serviceId?: string;
  modelType?: number;
  info?: string;
  label?: string;
  value?: string;
}

/** Validate the fields consumed by the authorization form at the API boundary. */
export function readModelAuthStatus(value: unknown): ModelVersion[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item: unknown): ModelVersion[] => {
    if (
      typeof item !== 'object' ||
      item === null ||
      !('domain' in item) ||
      typeof item.domain !== 'string' ||
      !('name' in item) ||
      typeof item.name !== 'string' ||
      !('status' in item) ||
      typeof item.status !== 'number' ||
      !Number.isFinite(item.status)
    )
      return [];
    return [
      {
        domain: item.domain,
        name: item.name,
        status: item.status,
        serviceId:
          'serviceId' in item && typeof item.serviceId === 'string'
            ? item.serviceId
            : undefined,
        modelType:
          'modelType' in item && typeof item.modelType === 'number'
            ? item.modelType
            : undefined,
        info:
          'info' in item && typeof item.info === 'string'
            ? item.info
            : undefined,
      },
    ];
  });
}
