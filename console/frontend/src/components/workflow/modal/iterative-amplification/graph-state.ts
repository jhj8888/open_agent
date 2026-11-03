import type { OnSelectionChangeParams, XYPosition } from 'reactflow';
import type {
  WorkflowNode,
  WorkflowSnapshot,
} from '@/components/workflow/types/domain';

/** ReactFlow selections carry generic node data; resolve IDs against our store. */
export function workflowSelection(
  selection: OnSelectionChangeParams,
  graph: WorkflowSnapshot
): WorkflowSnapshot {
  const nodeIds = new Set(selection.nodes.map(node => node.id));
  const edgeIds = new Set(selection.edges.map(edge => edge.id));
  return {
    nodes: graph.nodes.filter(node => nodeIds.has(node.id)),
    edges: graph.edges.filter(edge => edgeIds.has(edge.id)),
  };
}

export function leftmostPosition(
  nodes: WorkflowNode[]
): XYPosition | undefined {
  let result: XYPosition | undefined;
  for (const node of nodes) {
    if (!result || node.position.x < result.x) result = node.position;
  }
  return result;
}

export function iteratorCanvasNodes(
  nodes: WorkflowNode[],
  iteratorId: string,
  disabled: boolean
): WorkflowNode[] {
  return nodes
    .filter(node => node.data.parentId === iteratorId)
    .map(node => ({
      ...node,
      draggable: !disabled,
      position: node.data.originPosition ?? node.position,
      data: { ...node.data, parentId: '' },
      parentId: '',
      extent: undefined,
      zIndex: 0,
    }));
}
