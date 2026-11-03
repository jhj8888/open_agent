import {
  Connection,
  Edge,
  OnEdgesChange,
  OnNodesChange,
  ReactFlowInstance,
  Viewport,
} from 'reactflow';
import type { StoreApi } from 'zustand';
import type {
  WorkflowNode,
  WorkflowNodeData,
  WorkflowSnapshot,
} from '../../domain';

export type FlowState = {
  template?: string;
  input_keys?: object;
  memory_keys?: Array<string>;
  handle_keys?: Array<string>;
};

export type NodeType = WorkflowNode;

export type FlowStoreType = {
  loadHistory: (nodes: NodeType[], edges: Edge[]) => void;
  zoom: number;
  setZoom: (zoom: number) => void;
  reactFlowInstance: ReactFlowInstance<WorkflowNodeData> | null;
  setReactFlowInstance: (newState: ReactFlowInstance<WorkflowNodeData>) => void;
  flowState: FlowState | undefined;
  nodes: NodeType[];
  edges: Edge[];
  onNodesChange: OnNodesChange;
  onEdgesChange: OnEdgesChange;
  deleteNodeRef: (nodeId: string, outputId: string) => void;
  setNodes: (
    update: NodeType[] | ((oldState: NodeType[]) => NodeType[])
  ) => void;
  setEdges: (
    update: Edge[] | ((oldState: Edge[]) => Edge[]),
    noNeedTakeSnapshot?: boolean
  ) => void;
  setNode: (
    id: string,
    update: NodeType | ((oldState: NodeType) => NodeType)
  ) => void;
  delayCheckNode: (id: string) => void;
  checkNode: (id: string) => boolean;
  deleteNode: (nodeId: string) => void;
  paste: () => Promise<void>;
  onConnect: (connection: Connection) => void;
  removeNodeRef: (
    souceId: string,
    targetId: string,
    inputEdges?: Edge[]
  ) => void;
  updateNodeRef: (id: string) => void;
  delayUpdateNodeRef: (id: string) => void;
  switchNodeRef: (connection: Connection, oldEdge: Edge) => void;
  moveToPosition: (viewport: Viewport) => void;
  updateNodeNameStatus: (id: string, labelInput?: string) => void;
  reNameNode: (id: string, value: string) => void;
  copyNode: (id: string) => void;
  takeSnapshot: (flag?: boolean) => void;
  undo: () => void;
  historys: WorkflowSnapshot[];
  setHistorys: (
    update:
      | WorkflowSnapshot[]
      | ((oldState: WorkflowSnapshot[]) => WorkflowSnapshot[])
  ) => void;
};

export type FlowGetter = () => FlowStoreType;
export type FlowSetter = StoreApi<FlowStoreType>['setState'];
