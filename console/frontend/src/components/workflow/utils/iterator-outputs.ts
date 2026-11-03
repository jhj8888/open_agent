import { v4 as uuid } from 'uuid';
import type { WorkflowInput } from '../types/domain';

/** Keep the iterator end input aligned with a top-level container output. */
export function syncIteratorEndInputs(
  inputs: WorkflowInput[],
  operation: 'add' | 'remove' | 'replace',
  outputIndex: number,
  outputName?: string
): WorkflowInput[] {
  if (operation === 'add') {
    return [
      ...inputs,
      {
        id: uuid(),
        name: outputName ?? '',
        schema: { type: '', value: { type: 'ref', content: {} } },
      },
    ];
  }
  if (outputIndex < 0 || outputIndex >= inputs.length) return inputs;
  if (operation === 'remove') {
    return inputs.filter((_, index) => index !== outputIndex);
  }
  if (outputName === undefined) return inputs;
  return inputs.map((input, index) =>
    index === outputIndex ? { ...input, name: outputName } : input
  );
}
