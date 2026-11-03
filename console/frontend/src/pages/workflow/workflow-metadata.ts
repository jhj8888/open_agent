/** Read the serialized workflow extension without losing decimal string IDs. */
export function workflowBotId(
  serialized: string | null | undefined
): string | number | undefined {
  if (!serialized) return undefined;
  try {
    const value: unknown = JSON.parse(serialized);
    if (typeof value !== 'object' || value === null || !('botId' in value))
      return undefined;
    const id = value.botId;
    return (typeof id === 'string' && id.length > 0) ||
      (typeof id === 'number' && Number.isFinite(id))
      ? id
      : undefined;
  } catch {
    return undefined;
  }
}

/** The legacy publish modal requires a numeric bot ID; reject lossy conversion. */
export function workflowBotNumber(
  serialized: string | null | undefined
): number | undefined {
  const id = workflowBotId(serialized);
  if (id === undefined || (typeof id === 'string' && !/^\d+$/.test(id)))
    return undefined;
  const number = typeof id === 'number' ? id : Number(id);
  return Number.isSafeInteger(number) ? number : undefined;
}
