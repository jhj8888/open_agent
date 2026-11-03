import type { WorkflowInput } from '@/components/workflow/types/domain';
import { v4 as uuid } from 'uuid';
import { RpaParameter } from '@/types/rpa';
export const transRpaParameters = (
  parameters: RpaParameter[]
): WorkflowInput[] => {
  return parameters.map(
    (item): WorkflowInput => ({
      id: uuid(),
      name: item.varName,
      type: item.type,
      disabled: false,
      required: false,
      description: item.varDescribe,
      schema: {
        type: item.type,
        value: {
          type: 'ref',
          content: {},
        },
      },
    })
  );
};
