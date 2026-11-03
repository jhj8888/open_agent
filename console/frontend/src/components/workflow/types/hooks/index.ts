import type {
  WorkflowNode,
  WorkflowNodeData,
  WorkflowNodeParameters,
  WorkflowInput,
  WorkflowOutput,
  WorkflowReference,
  ParameterProperty,
  ParameterSchema,
} from '../domain';
import type { WorkflowModel } from '../domain';
import { RpaNodeParam } from '@/types/rpa';
import {
  AddNodeType,
  ToolType,
  McpType,
  FlowType,
  PositionType,
  NewNodeType,
} from '../drawer/chat-debugger';
import React from 'react';

// Hook 相关类型定义

// useFlowTypeRender Hook 相关类型
export type ItemType = Pick<ParameterProperty, 'fileType' | 'type' | 'schema'>;

// useNodeCommon Hook 相关类型
export interface NodeCommonProps {
  id: string;
  data?: NodeDataType;
}

export interface UseNodeInfoReturn {
  nodeType: string;
  isStartNode: boolean;
  isIteratorStart: boolean;
  isEndNode: boolean;
  isIteratorEnd: boolean;
  isKnowledgeNode: boolean;
  isQuestionAnswerNode: boolean;
  isDecisionMakingNode: boolean;
  isIfElseNode: boolean;
  isIteratorNode: boolean;
  isLoopNode: boolean;
  isIteratorChildNode: boolean;
  isAgentNode: boolean;
  isStartOrEndNode: boolean;
  isCodeNode: boolean;
  isDataBaseNode: boolean;
  showInputs: boolean;
  showOutputs: boolean;
  showExceptionFlow: boolean;
  references: ReferenceItem[];
  inputs: InputItem[];
  outputs: OutputItem[];
  showNodeOperation: boolean;
  currentNode?: WorkflowNode;
  nodeParam: WorkflowNodeParameters;
  nodeIcon?: string;
  nodeDesciption?: string;
  isFixedInputsNode: boolean;
  isRpaNode: boolean;
  inputLabel: string;
  outputLabel: string;
  allowAddInput: boolean;
  allowAddOutput: boolean;
}

export interface UseNodeFuncReturn {
  handleNodeClick: () => void;
  handleChangeNodeParam: <Value>(
    fn: (data: NodeDataType, value: Value) => void,
    value: Value
  ) => void;
  handleChangeOutputParam: <Value>(
    outputId: string,
    fn: (data: PropertyItem, value: Value, nodeData: WorkflowNodeData) => void,
    value: Value
  ) => void;
  handleIteratorEndChange: (
    type: 'add' | 'remove' | 'replace',
    outputId: string,
    value?: unknown,
    currentNode?: WorkflowNode
  ) => void;
  handleAddOutputLine: () => void;
  handleRemoveOutputLine: (outputId: string) => void;
  isFixedOutputComponentFunc: (output: PropertyItem) => boolean;
}

export interface UseNodeOutputRenderReturn {
  handleCustomOutputGenerate: () => void;
  renderOutputComponent: (output: PropertyItem) => React.ReactElement;
  outputTypeList: Array<{
    label: string;
    value: string;
    children?: Array<{
      label: string;
      value: string;
    }>;
  }>;
}

export interface UseNodeModelsReturn {
  models: WorkflowModel[];
  model: WorkflowModel | undefined;
  isThinkModel: boolean;
}

export type NodeDataType = WorkflowNodeData;

export type InputItem = WorkflowInput;

export type OutputItem = WorkflowOutput;

export type PropertyItem = ParameterProperty;

export type ReferenceItem = WorkflowReference;

export type SchemaType = ParameterSchema;

export interface RetryConfig {
  shouldRetry?: boolean;
  errorStrategy?: number;
  customOutput?: string;
}

export interface UseNodeCommonReturn {
  isFixedInputsNode: boolean;
  allowNoInputParams: boolean;
  isLoopNode: boolean;
  isEndNode: boolean;
  handleNodeClick: () => void;
  handleChangeNodeParam: <Value>(
    fn: (data: NodeDataType, value: Value) => void,
    value: Value
  ) => void;
  handleChangeInputParam: <Value>(
    inputId: string,
    fn: (data: InputItem, value: Value, nodeData: WorkflowNodeData) => void,
    value: Value
  ) => void;
  handleChangeOutputParam: <Value>(
    outputId: string,
    fn: (data: PropertyItem, value: Value, nodeData: WorkflowNodeData) => void,
    value: Value
  ) => void;
  handleAddOutputLine: () => void;
  handleRemoveOutputLine: (outputId: string) => void;
  handleCustomOutputGenerate: () => void;
  titleRender: (nodeData: {
    name: string;
    schema?: SchemaType;
    type?: string;
  }) => React.ReactElement;
  renderTypeInput: (output: PropertyItem) => React.ReactElement;
  addUniqueComponentToProperties: (schemasArray: OutputItem[]) => OutputItem[];
  renderTypeOneClickUpdate: () => React.ReactElement | null;
  handleAddInputLine: () => void;
  handleRemoveInputLine: (inputId: string) => void;
  nodeType: string;
  isConnectable: boolean;
  nodeParam: WorkflowNodeParameters;
  canvasesDisabled: boolean;
  isStartNode: boolean;
  hasTargetHandle: boolean;
  hasSourceHandle: boolean;
  sourceHandleId?: string;
  exceptionHandleId?: string;
  model?: WorkflowModel;
  nodeIcon?: string;
  nodeDesciption?: string;
  isIteratorStart: boolean;
  isIteratorEnd: boolean;
  isKnowledgeNode: boolean;
  isQuestionAnswerNode: boolean;
  isDecisionMakingNode: boolean;
  isIfElseNode: boolean;
  isIteratorNode: boolean;
  isIteratorChildNode: boolean;
  isAgentNode: boolean;
  isStartOrEndNode: boolean;
  isRpaNode?: boolean;
  isCodeNode: boolean;
  showInputs: boolean;
  showOutputs: boolean;
  showExceptionFlow: boolean;
  references: ReferenceItem[];
  inputs: InputItem[];
  outputs: OutputItem[];
  showNodeOperation: boolean;
  currentNode?: WorkflowNode;
  models: WorkflowModel[];
  outputTypeList: Array<{
    label: string;
    value: string;
    children?: Array<{
      label: string;
      value: string;
    }>;
  }>;
  isThinkModel: boolean;
  inputLabel: string;
  outputLabel: string;
  allowAddInput: boolean;
  allowAddOutput: boolean;
}

export interface UseFlowCommonReturn {
  startWorkflowKeydownEvent: boolean;
  startIterativeWorkflowKeydownEvent: boolean;
  handleAddNode: (
    addNode: AddNodeType,
    position: PositionType
  ) => NewNodeType[] | null;
  handleAddToolNode: (tool: ToolType) => void;
  handleAddMcpNode: (mcp: McpType) => void;
  handleAddFlowNode: (flow: FlowType) => void;
  handleAddRpaNode: (rpa: RpaNodeParam) => void;
  handleEdgeAddNode: (
    addNode: AddNodeType,
    position: PositionType,
    sourceHandle: string | null,
    currentNode: NewNodeType
  ) => void;
  handleDebugger: () => void;
  resetBeforeAndWillNode: () => void;
}

export interface UseNodeHandleReturn {
  isConnectable: boolean;
  hasSourceHandle: boolean;
  hasTargetHandle: boolean;
  sourceHandleId?: string;
  exceptionHandleId?: string;
}

export interface UseNodeInputRenderReturn {
  allowNoInputParams: boolean;
  renderTypeInput: (output: PropertyItem) => React.ReactElement;
  handleChangeInputParam: <Value>(
    inputId: string,
    fn: (data: InputItem, value: Value, nodeData: WorkflowNodeData) => void,
    value: Value
  ) => void;
  handleAddInputLine: () => void;
  handleRemoveInputLine: (inputId: string) => void;
}

export interface UseVariableMemoryHandlersReturn {
  updateVariableMemoryNodeRef: () => void;
  handleChangeParam: <Value>(
    outputId: string,
    fn: (data: InputItem, value: Value, nodeData: WorkflowNodeData) => void,
    value: Value
  ) => void;
  handleRemoveInputLine: (inputId: string) => void;
}

export interface UseAddNodeReturn {
  handleAddNode: (
    addNode: AddNodeType,
    position: PositionType
  ) => NewNodeType[] | null;
}

export interface UseAddToolNodeReturn {
  handleAddToolNode: (tool: ToolType) => void;
}

export interface UseAddMcpNodeReturn {
  handleAddMcpNode: (mcp: McpType) => void;
}

export interface UseAddFlowNodeReturn {
  handleAddFlowNode: (flow: FlowType) => void;
}

export interface UseAddRpaNodeReturn {
  handleAddRpaNode: (rpa: RpaNodeParam) => void;
}

// 重新导出常用的类型以便在hooks中使用
export type {
  AddNodeType,
  ToolType,
  McpType,
  FlowType,
  PositionType,
  NewNodeType,
} from '../drawer/chat-debugger';
