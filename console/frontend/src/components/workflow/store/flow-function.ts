import {
  getNodeId,
  getEdgeId,
  extractTargetAndSource,
  checkedNodeInputData,
  checkedNodeOutputData,
  checkedNodeParams,
  findChildrenNodes,
  findParentNodes,
  getNextName,
  handleReplaceNodeId,
  generateReferences,
} from '@/components/workflow/utils/reactflowUtils';
import { v4 as uuid } from 'uuid';
import { message } from 'antd';
import { cloneDeep } from 'lodash';
import useFlowsManager from './use-flows-manager';
import {
  Edge,
  EdgeChange,
  NodeChange,
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  MarkerType,
  Connection,
} from 'reactflow';
import type {
  WorkflowNode as Node,
  WorkflowSnapshot,
  WorkflowViewport,
  WorkflowNodeData,
  WorkflowInput,
  WorkflowReference,
} from '../types/domain';
import type {
  FlowGetter,
  FlowSetter,
  FlowStoreType,
} from '../types/zustand/flow';

export const initialStatus: Pick<
  FlowStoreType,
  'historys' | 'nodes' | 'edges' | 'zoom'
> = {
  historys: [], //History List
  nodes: [], //Node List
  edges: [], //Edge List
  zoom: 80, //Zoom
};

// Undo
const undo = (get: FlowGetter): void => {
  const history = get().historys?.[get().historys.length - 1];
  if (history) {
    const currentStore = useFlowsManager.getState().getCurrentStore();
    currentStore.getState().loadHistory(history?.nodes, history?.edges);
    get().setHistorys(history => {
      history.pop();
      return cloneDeep(history);
    });
  }
};

// Set Zoom
const setZoom = (zoom: number, set: FlowSetter): void => {
  set({
    zoom,
  });
};

// Take Snapshot
const takeSnapshot = (get: FlowGetter): void => {
  const currentStore = useFlowsManager.getState().getCurrentStore();
  const flowStore = currentStore.getState();
  const newState = {
    nodes: cloneDeep(flowStore.nodes),
    edges: cloneDeep(flowStore.edges),
  };
  get().setHistorys(history => cloneDeep([...history, newState]));
};

// Set Historys
const setHistorys = (
  change: unknown,
  get: FlowGetter,
  set: FlowSetter
): void => {
  const newChange =
    typeof change === 'function' ? change(get().historys) : change;
  set({
    historys: newChange,
  });
};

// Move to Position
const moveToPosition = (viewport: WorkflowViewport): void => {
  const flowStore = useFlowsManager?.getState?.();
  const currentStore = flowStore?.getCurrentStore?.();
  const currentState = currentStore?.getState?.();
  const reactFlowInstance = currentState?.reactFlowInstance;
  if (reactFlowInstance) {
    reactFlowInstance.setViewport(viewport);
  }
};

// Set React Flow Instance
const setReactFlowInstance = (
  newState: Parameters<FlowStoreType['setReactFlowInstance']>[0],
  set: FlowSetter
): void => {
  set({ reactFlowInstance: newState });
};

// On Nodes Change
const onNodesChange = (
  changes: NodeChange[],
  get: FlowGetter,
  set: FlowSetter
): void => {
  set({
    nodes: applyNodeChanges<WorkflowNodeData>(changes, get().nodes).map(
      node => ({
        ...node,
        nodeType:
          'nodeType' in node && typeof node.nodeType === 'string'
            ? node.nodeType
            : (node.id.split('::')[0] ?? ''),
      })
    ),
  });
};

// On Edges Change
const onEdgesChange = (
  changes: EdgeChange[],
  get: FlowGetter,
  set: FlowSetter
): void => {
  const change = changes[0];
  if (change?.type === 'remove') {
    get()?.takeSnapshot();
    const [source, target] = extractTargetAndSource(change.id) ?? [];
    if (source && target) get().removeNodeRef(source, target);
  }
  set({
    edges: applyEdgeChanges(changes, get().edges),
  });
};

// Set Nodes
const setNodes = (
  change: Parameters<FlowStoreType['setNodes']>[0],
  get: FlowGetter,
  set: FlowSetter
): void => {
  const newChange = typeof change === 'function' ? change(get().nodes) : change;
  const newEdges = cloneDeep(get().edges);
  set({
    edges: newEdges,
    nodes: newChange,
  });
};

// Set Edges
const setEdges = (
  change: Parameters<FlowStoreType['setEdges']>[0],
  get: FlowGetter,
  set: FlowSetter
): void => {
  const newChange = typeof change === 'function' ? change(get().edges) : change;
  set({
    edges: newChange,
  });
};

// Set Node
const setNode = (
  id: string,
  change: Node | ((oldState: Node) => Node),
  get: FlowGetter,
  set: FlowSetter
): void => {
  const currentNode = get().nodes.find(node => node.id === id);
  if (!currentNode) return;
  const newChange = typeof change === 'function' ? change(currentNode) : change;

  get().setNodes((oldNodes: Node[]) =>
    oldNodes.map((node: Node) => {
      if (node.id === id) {
        return newChange;
      }
      return node;
    })
  );
};

// Delay Check Node
const delayCheckNode = (nodeId: string, get: FlowGetter): void => {
  setTimeout(() => {
    checkNode(nodeId, get);
  }, 500);
};

// Check Node
const checkNode = (nodeId: string, get: FlowGetter): boolean => {
  const currentCheckNode = get().nodes.find(node => node.id === nodeId);
  if (!currentCheckNode) return false;
  const inputsFlag = checkedNodeInputData(
    currentCheckNode.data.inputs || [],
    currentCheckNode
  );
  const outputsFlag = checkedNodeOutputData(
    currentCheckNode.data.outputs || [],
    currentCheckNode
  );
  const paramsFlag = checkedNodeParams(currentCheckNode);
  const repeatedFlag = true;
  const checkFlag = inputsFlag && outputsFlag && paramsFlag && repeatedFlag;
  get().setNode(nodeId, cloneDeep(currentCheckNode));
  useFlowsManager.getState().autoSaveCurrentFlow();
  return checkFlag;
};
// Copy Node
const copyNode = (nodeId: string, get: FlowGetter): void => {
  get()?.takeSnapshot();
  const currentNode = get().nodes.find(item => item.id === nodeId);
  if (!currentNode) return;
  const currentTypeList = get().nodes.filter(
    node => node.nodeType === currentNode.nodeType
  );
  currentNode.selected = false;
  const copyNode = cloneDeep(currentNode);
  copyNode.id = getNodeId(copyNode.id.split('::')[0] ?? copyNode.nodeType);
  copyNode.data.label = getNextName(
    currentTypeList,
    currentNode.data.label?.split('_')[0] ?? currentNode.data.nodeMeta.aliasName
  );
  copyNode.data.inputs = copyNode.data.inputs?.map(input => ({
    id: input?.id,
    name: input?.name,
    required: input?.required,
    type: input?.type,
    schema: {
      type: 'string',
      value:
        input.schema.value.type === 'literal'
          ? { ...input.schema.value }
          : { type: 'ref', content: {} },
    },
  }));
  copyNode.data.references = [];
  copyNode.data.shrink = false;
  copyNode.position = {
    x: copyNode.position.x + 50,
    y: copyNode.position.y + 50,
  };
  copyNode.selected = true;
  if (['iteration', 'loop'].includes(currentNode?.nodeType)) {
    const idsMap: Record<string, string> = {};
    const childNodes = get()?.nodes?.filter(
      node => node?.data?.parentId === currentNode?.id
    );
    const newChildNodes = handleReplaceNodeId(
      childNodes?.map(item => {
        const newId = getNodeId(item.id.split('::')[0] ?? item.nodeType);
        idsMap[item.id] = newId;
        return {
          ...item,
          id: newId,
          parentId: copyNode?.id,
          data: {
            ...item?.data,
            parentId: copyNode?.id,
          },
        };
      }),
      idsMap
    );
    const startNodeType =
      currentNode?.nodeType === 'loop'
        ? 'loop-node-start'
        : 'iteration-node-start';
    const iterationNodeStartKey = Object.keys(idsMap)?.find(item =>
      item?.startsWith(startNodeType)
    );
    if (currentNode?.nodeType === 'loop') {
      copyNode.data.nodeParam.LoopStartNodeId =
        idsMap[iterationNodeStartKey as string];
    } else {
      copyNode.data.nodeParam.IterationStartNodeId =
        idsMap[iterationNodeStartKey as string];
    }
    get().setNodes(old => {
      return cloneDeep([
        ...old.map(item => ({ ...item, selected: false })),
        copyNode,
        ...newChildNodes,
      ]);
    });
    const childNodesId = childNodes?.map(node => node?.id);
    const newEdges = get()
      .edges?.filter(
        edge =>
          childNodesId?.includes(edge?.target) ||
          childNodesId?.includes(edge?.source)
      )
      ?.map(edge => ({
        ...edge,
        id: getEdgeId(
          idsMap[edge.target] ?? edge.target,
          idsMap[edge.source] ?? edge.source
        ),
        target: idsMap[edge.target] ?? edge.target,
        source: idsMap[edge.source] ?? edge.source,
        selected: false,
      }));
    get().setEdges(oldEdges => cloneDeep([...oldEdges, ...newEdges]));
  } else {
    get().setNodes(old => {
      return cloneDeep([
        ...old.map(item => ({ ...item, selected: false })),
        copyNode,
      ]);
    });
  }
};

// Delete Node
const deleteNode = (nodeId: string, get: FlowGetter): void => {
  get()?.takeSnapshot();
  const currentNode = get().nodes?.find(node => node?.id === nodeId);
  if (!currentNode) return;
  const willDeleteNodeIds = get()
    .nodes?.filter(
      node =>
        node?.data?.parentId === currentNode?.id || node?.id === currentNode?.id
    )
    ?.map(node => node?.id);
  const newEdges = get().edges.filter(
    edge =>
      !willDeleteNodeIds?.includes(edge.target) &&
      !willDeleteNodeIds?.includes(edge.source)
  );
  get().edges.forEach(edge => {
    if (
      willDeleteNodeIds?.includes(edge.target) ||
      willDeleteNodeIds?.includes(edge.source)
    ) {
      get().removeNodeRef(edge.source, edge.target, [edge, ...newEdges]);
    }
  });
  get().setNodes(
    !['iteration', 'loop'].includes(currentNode?.nodeType)
      ? get().nodes.filter(node => node.id !== nodeId)
      : get().nodes.filter(node => !willDeleteNodeIds?.includes(node?.id))
  );

  get().setEdges(newEdges);

  useFlowsManager.getState().autoSaveCurrentFlow();
  useFlowsManager.getState().setNodeInfoEditDrawerlInfo({
    open: false,
    nodeId: '',
  });
};

// Update Node Name Status
const updateNodeNameStatus = (
  nodeId: string,
  labelInputId: string | undefined,
  get: FlowGetter
): void => {
  get().setNodes((nodes: Node[]) => {
    const targetNode = nodes.find(item => item?.id === nodeId);
    if (!targetNode) return nodes;
    targetNode.data.labelEdit = !targetNode.data.labelEdit;
    if (targetNode.data.labelEdit) {
      setTimeout(() => {
        if (labelInputId) document.getElementById(labelInputId)?.focus();
      }, 100);
    } else {
      setTimeout(() => {
        get().updateNodeRef(nodeId);
      }, 500);
    }
    return cloneDeep(nodes);
  });
};

// Re Name Node
const reNameNode = (nodeId: string, value: string, get: FlowGetter): void => {
  get().setNodes((nodes: Node[]) => {
    const targetNode = nodes.find(item => item?.id === nodeId);
    if (!targetNode) return nodes;
    targetNode.data.label = value;
    return cloneDeep(nodes);
  });
};

// Paste
const paste = async (get: FlowGetter): Promise<void> => {
  try {
    const text = await navigator.clipboard.readText();
    const selection: WorkflowSnapshot = JSON.parse(text);
    const idsMap: Record<string, string> = {};
    let newNodes: Node[] = get().nodes;
    const currentTypeNodeList = cloneDeep(get().nodes);

    newNodes = selection?.nodes.map(item => {
      const currentTypeList = currentTypeNodeList.filter(
        node =>
          node.data?.label?.split('_')?.[0] === item.data.label?.split('_')[0]
      );
      const newId = getNodeId(item.id.split('::')[0] ?? item.nodeType);
      idsMap[item.id] = newId;
      item.data.label = getNextName(
        currentTypeList,
        item.data.label?.split('_')[0] ?? item.data.nodeMeta.aliasName
      );
      item.data.inputs = item.data.inputs?.map(input => ({
        id: uuid(),
        name: input?.name,
        required: input?.required,
        type: input?.type,
        schema: {
          type: 'string',
          value:
            input.schema.value.type === 'literal'
              ? { ...input.schema.value }
              : { type: 'ref', content: {} },
        },
      }));
      item.data.references = [];
      item.data.shrink = false;
      currentTypeNodeList.push(item);
      const newItem = {
        ...item,
        id: newId,
        position: {
          x: item.position.x + 50,
          y: item.position.y + 50,
        },
        selected: true,
      };
      if (item?.parentId) {
        newItem.parentId = idsMap[item.parentId];
      }
      if (item?.data?.parentId) {
        newItem.data.parentId = idsMap[item.data.parentId];
      }
      return newItem;
    });
    get().setNodes(old => {
      return cloneDeep([
        ...(old?.map(item => ({ ...item, selected: false })) || []),
        ...newNodes,
      ]);
    });
    const newEdges = selection.edges
      ?.filter(edge => idsMap[edge.target] && idsMap[edge.source])
      ?.map(edge => ({
        ...edge,
        id: getEdgeId(
          idsMap[edge.target] ?? edge.target,
          idsMap[edge.source] ?? edge.source
        ),
        target: idsMap[edge.target] ?? edge.target,
        source: idsMap[edge.source] ?? edge.source,
        selected: false,
      }));

    get().setEdges(oldEdges => cloneDeep([...oldEdges, ...newEdges]));

    setTimeout(() => {
      newNodes.forEach(item => {
        get().updateNodeRef(item.id);
      });
    }, 500);
  } catch (error) {
    console.error('[Clipboard] 复制失败', error);
    message.error('[Clipboard] 复制失败');
    return;
  }
};

// Function to update node references
const updateNodeRef = (id: string, get: FlowGetter): void => {
  const childrenNodes: string[] = findChildrenNodes(id, get().edges);

  get().setNodes(old => {
    old.forEach(item => {
      if (!childrenNodes.includes(item.id)) {
        return;
      }

      const references = generateReferences(get().nodes, get().edges, item?.id);

      item.data?.inputs?.forEach(input => {
        processInputReference(item, input, references);
      });

      if (item?.nodeType === 'iteration') {
        updateIterationOutputs(item, old);
      }
      if (item?.nodeType === 'loop') {
        updateLoopOutputs(item, old);
      }
    });

    return cloneDeep(old);
  });

  const state = useFlowsManager.getState();
  state.canPublishSetNot();
  state.autoSaveCurrentFlow();
};
//
// Process Input Reference
function processInputReference(
  item: Node,
  input: WorkflowInput,
  references: WorkflowReference[]
): void {
  if (input.schema.value.type !== 'ref') return;
  const content = input.schema.value.content;
  const node = references?.find(ref => ref.value === content.nodeId);

  const nodeReferences = node?.children?.[0]?.references || [];
  const reference =
    nodeReferences.find(reference => reference.id === content.id) ||
    findReferenceByValue(nodeReferences, content.name);

  if (shouldResetIteration(item, input, reference)) {
    resetContent(input);
  } else if (node && reference) {
    applyReference(item, input, reference, node.value);
  } else if (typeof input.schema.value.content === 'object') {
    resetContent(input);
  }
}

function findReferenceByValue(
  references: WorkflowReference[],
  value: string | undefined
): WorkflowReference | null {
  if (!value) {
    return null;
  }

  for (const reference of references) {
    if (reference?.value === value) {
      return reference;
    }

    const childReference = findReferenceByValue(
      reference?.children || [],
      value
    );
    if (childReference) {
      return childReference;
    }
  }

  return null;
}
// Should Reset Iteration
function shouldResetIteration(
  item: Node,
  input: WorkflowInput,
  reference: WorkflowReference | null | undefined
): boolean {
  return (
    item?.nodeType === 'iteration' &&
    typeof input.schema.value.content === 'object' &&
    !reference?.type?.includes('array')
  );
}

// Reset Content
function resetContent(input: WorkflowInput): void {
  if (input.schema.value.type !== 'ref') return;
  input.schema.value.content.id = '';
  input.schema.value.content.name = '';
  input.schema.value.content.nodeId = '';
}

// Apply Reference
function applyReference(
  item: Node,
  input: WorkflowInput,
  reference: WorkflowReference,
  nodeId: string
): void {
  if (input.schema.value.type !== 'ref') return;
  input.schema.value.content.id = reference?.id;
  input.schema.value.content.name = reference?.value;
  input.schema.value.content.nodeId = nodeId;
  if (item?.nodeType !== 'plugin' && item?.nodeType !== 'flow') {
    input.schema.type = reference.type ?? 'string';
    input.fileType = reference?.fileType;
  }
}

// Update Iteration Outputs
function updateIterationOutputs(item: Node, old: Node[]): void {
  const outputs = item?.data?.inputs?.map(input => ({
    id: input?.id,
    name: input?.name,
    schema: {
      type: input.schema.type.split('-').pop() ?? 'string',
      default: '',
    },
  }));

  const iteratorStartNode = old?.find(
    node => node?.data?.parentId === item?.id && node?.nodeType === 'node-start'
  );

  if (iteratorStartNode) {
    iteratorStartNode.data.outputs = outputs;
  }
}

function updateLoopOutputs(item: Node, old: Node[]): void {
  const loopVariables = item?.data?.nodeParam?.loopVariables || [];
  const outputs = loopVariables?.map(variable => ({
    id: variable?.id || uuid(),
    name: variable?.name,
    schema: variable?.schema || {
      type: 'string',
      default: '',
    },
  }));

  const loopStartNode = old?.find(
    node =>
      node?.data?.parentId === item?.id && node?.nodeType === 'loop-node-start'
  );

  if (loopStartNode) {
    loopStartNode.data.outputs = outputs;
  }
}

// Function to delay updating node references
const delayUpdateNodeRef = (id: string, get: FlowGetter): void => {
  setTimeout(() => {
    get().updateNodeRef(id);
  }, 500);
};

// Function to remove node references
const removeNodeRef = (
  souceId: string,
  targetId: string,
  inputEdges: Edge[] | undefined,
  get: FlowGetter
): void => {
  const edges = (inputEdges || get().edges).filter(
    edge => edge.target !== targetId || edge.source !== souceId
  );
  const childrenNodes: string[] = findChildrenNodes(
    souceId,
    inputEdges || get().edges
  );
  get().setNodes((old: Node[]) => {
    old.forEach(node => {
      if (childrenNodes.includes(node.id)) {
        const parentNodes: string[] = findParentNodes(node.id, edges);
        node.data?.inputs?.forEach(input => {
          if (input.schema.value.type !== 'ref') return;
          const inputId = input.schema.value.content.nodeId;
          if (inputId && !parentNodes.includes(inputId)) {
            input.schema.value.content = {
              name: '',
              nodeId: '',
            };
          }
        });
      }
    });
    return cloneDeep(old);
  });
  useFlowsManager.getState().canPublishSetNot();
};

// Function to delete node references
const deleteNodeRef = (id: string, outputId: string, get: FlowGetter): void => {
  const childrenNodes: string[] = findChildrenNodes(id, get().edges);
  get().setNodes(old => {
    old.forEach(item => {
      if (childrenNodes.includes(item?.id)) {
        item.data?.inputs?.forEach(input => {
          if (
            input.schema.value.type === 'ref' &&
            input.schema.value.content.id === outputId
          ) {
            input.schema.value.content = {};
          }
        });
      }
    });
    return cloneDeep(old);
  });
  useFlowsManager.getState().canPublishSetNot();
};

// Function to switch node references
const switchNodeRef = (
  connection: Connection,
  oldEdge: Edge,
  get: FlowGetter
): void => {
  get().removeNodeRef(oldEdge.source, oldEdge.target);
};

// Function to add intent ID
const addIntentId = (connection: Edge, get: FlowGetter): void => {
  const sourceNode = get().nodes?.find(item => item.id === connection.source);
  if (sourceNode) get().setNode(connection.source, cloneDeep(sourceNode));
};

// Function to handle connection
const onConnect = (connection: Connection, get: FlowGetter): void => {
  let newEdges: Edge[] = [];
  get()?.takeSnapshot();
  get().setEdges((oldEdges: Edge[]) => {
    newEdges = addEdge(
      {
        ...connection,
        type: 'customEdge',
        markerEnd: {
          type: MarkerType.Arrow,
          color: '#6356EA',
        },
        data: {
          edgeType: useFlowsManager.getState().edgeType,
        },
      },
      oldEdges
    );
    return newEdges;
  });
};

// Function to load history
const loadHistory = (nodes: Node[], edges: Edge[], set: FlowSetter): void => {
  set({
    nodes,
    edges,
  });
};

export {
  undo,
  setZoom,
  takeSnapshot,
  setHistorys,
  moveToPosition,
  setReactFlowInstance,
  onNodesChange,
  onEdgesChange,
  setNodes,
  setEdges,
  setNode,
  delayCheckNode,
  checkNode,
  copyNode,
  deleteNode,
  updateNodeNameStatus,
  reNameNode,
  paste,
  updateNodeRef,
  delayUpdateNodeRef,
  removeNodeRef,
  deleteNodeRef,
  switchNodeRef,
  addIntentId,
  onConnect,
  loadHistory,
};
