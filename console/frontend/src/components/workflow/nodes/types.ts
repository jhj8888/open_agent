import type { ReactNode } from 'react';
import type { UseNodeCommonReturn } from '@/components/workflow/types/hooks';
import type { FlowStoreType } from '@/components/workflow/types/zustand/flow';
import type { FlowsManagerStoreType } from '@/components/workflow/types/zustand/flowsManager';
import type {
  WorkflowInput,
  WorkflowNodeData,
  WorkflowOutput,
  VariableReference,
} from '@/components/workflow/types/domain';

export interface NodeComponentProps {
  id: string;
  data: WorkflowNodeData;
  selected?: boolean;
}

export interface NodeDataProps {
  data: WorkflowNodeData;
  id?: string;
}

export interface NodeChildrenProps extends NodeComponentProps {
  children?: ReactNode;
}

export type ChangeNodeParameter = <Value>(
  update: (data: WorkflowNodeData, value: Value) => void,
  value: Value
) => void;

export type ChangeInputParameter = <Value>(
  id: string,
  update: (input: WorkflowInput, value: Value) => void,
  value: Value
) => void;

export type ChangeOutputParameter = <Value>(
  id: string,
  update: (output: WorkflowOutput, value: Value) => void,
  value: Value
) => void;

export interface NodeParameterProps extends NodeComponentProps {
  handleChangeNodeParam: ChangeNodeParameter;
}

type NodeEditorState = NodeChildrenProps &
  UseNodeCommonReturn &
  FlowStoreType &
  FlowsManagerStoreType;

export type NodePropsFor<Key extends keyof NodeEditorState> = Pick<
  NodeEditorState,
  Key
>;

export function inputReference(
  input: WorkflowInput | undefined
): VariableReference | undefined {
  return input?.schema.value.type === 'ref'
    ? input.schema.value.content
    : undefined;
}

export function changeReferencedInput<Value>(
  change: ChangeInputParameter,
  inputId: string | undefined,
  update: (input: WorkflowInput, value: Value) => void,
  value: Value
): void {
  if (inputId) change(inputId, update, value);
}
