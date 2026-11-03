import lodash from 'lodash';
import type {
  WorkflowNode,
  WorkflowNodeTemplate,
} from '@/components/workflow/types/domain';
const { cloneDeep } = lodash;

/** Instantiate fresh fixed nodes without modifying the reusable catalog. */
export function createResetNodes(
  templates: WorkflowNodeTemplate[] | undefined,
  createNodeId: (type: string) => string,
  createParameterId: () => string
): WorkflowNode[] | undefined {
  if (
    !templates?.some(node => node.idType === 'node-start') ||
    !templates.some(node => node.idType === 'node-end')
  )
    return undefined;
  return templates.map((template): WorkflowNode => {
    const node = cloneDeep(template);
    const id = createNodeId(node.idType);
    return {
      ...node,
      id,
      type: 'custom',
      nodeType: node.idType,
      position: node.position ?? {
        x: node.idType === 'node-end' ? 1000 : 100,
        y: 300,
      },
      data: {
        ...node.data,
        inputs: node.data.inputs.map(input => ({
          ...input,
          id: createParameterId(),
        })),
        outputs: node.data.outputs.map(output => ({
          ...output,
          id: createParameterId(),
        })),
      },
    };
  });
}
