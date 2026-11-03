import type { Dispatch, ReactNode, RefObject, SetStateAction } from 'react';
import type { Modal, RadioChangeEvent } from 'antd';
import type { TableField } from '@/types/database';
import type {
  WorkflowCase,
  WorkflowCondition,
  WorkflowInput,
} from '@/components/workflow/types/domain';
import type { NodeComponentProps, NodePropsFor } from '../types';

export type DatabaseField = Omit<TableField, 'id' | 'type'> & {
  id: string;
  type: string;
  required?: boolean;
};

export interface DatabaseFieldOption {
  value: string;
  name: string;
  label: string;
  type: string;
  required?: boolean;
  description?: string;
}

export interface DatabaseSelectedField {
  id: string;
  name: string;
  type: string;
  order: 'asc' | 'desc';
}

export interface DatabaseSheetOption {
  id: string | number;
  value: string;
  label: string;
}

export interface DatabaseTableOption {
  value: string;
  label: string;
  children: DatabaseSheetOption[];
}

type StoreProps = NodePropsFor<
  | 'nodeParam'
  | 'handleChangeNodeParam'
  | 'handleChangeInputParam'
  | 'handleRemoveInputLine'
  | 'references'
  | 'inputs'
  | 'historyVersion'
  | 'canvasesDisabled'
  | 'setNode'
  | 'setEdges'
  | 'edges'
  | 'checkNode'
  | 'delayCheckNode'
  | 'takeSnapshot'
  | 'autoSaveCurrentFlow'
  | 'canPublishSetNot'
  | 'updateNodeRef'
>;

export interface DatabaseEditorContext extends NodeComponentProps, StoreProps {
  children?: ReactNode;
  fields: DatabaseField[];
  allFields: DatabaseField[];
  setFields: Dispatch<SetStateAction<DatabaseField[]>>;
  setAllFields: Dispatch<SetStateAction<DatabaseField[]>>;
  allTable: DatabaseTableOption[];
  tab: number;
  setTab: Dispatch<SetStateAction<number>>;
  handleMode: number;
  setHandleMode: Dispatch<SetStateAction<number>>;
  mode?: number;
  handleDbChange: (id: string) => void;
  handleSheetChange: (values: (string | number)[]) => void;
  handleCustomSQL: () => void;
  handleformdata: () => void;
  modeChange: (mode: number) => void;
  getFields: (
    tables: DatabaseTableOption[],
    dbId: string,
    tableName: string
  ) => void;
  item: WorkflowCase;
  condition: WorkflowCondition;
  operatorId: string;
  setOperatorId: Dispatch<SetStateAction<string>>;
  operatorRef: RefObject<HTMLDivElement>;
  handleOperatorChange: (operator: string) => void;
  handleConditionChange: (value: string, condition: WorkflowCondition) => void;
  handleFieldChange: (value: string, condition: WorkflowCondition) => void;
  handleRemoveLine: (condition: WorkflowCondition) => void;
  handleNotInClick: (condition: WorkflowCondition) => Promise<void>;
  curentInput: (condition: WorkflowCondition) => WorkflowInput | undefined;
  getTextArray: (condition: WorkflowCondition) => string;
  fieldOptions: DatabaseFieldOption[];
  getFieldOptions: (condition?: string | null) => DatabaseFieldOption[];
  getConditionOptions: (
    type?: string | null
  ) => { label: string; value: string }[];
  setValidateMsg: Dispatch<SetStateAction<string>>;
  modal: ReturnType<typeof Modal.useModal>[0];
  from: 'query' | 'sort';
  addDataOptions: DatabaseFieldOption[];
  setAddDataOptions: Dispatch<SetStateAction<DatabaseFieldOption[]>>;
  fieldList: DatabaseSelectedField[];
  setFieldList: Dispatch<SetStateAction<DatabaseSelectedField[]>>;
}

export type DatabasePropsFor<Key extends keyof DatabaseEditorContext> = Pick<
  DatabaseEditorContext,
  Key
>;

export type DatabaseSortChange = (
  event: RadioChangeEvent,
  field: DatabaseSelectedField
) => void;

export function isPresent<Value>(
  value: Value | null | undefined
): value is Value {
  return value !== null && value !== undefined;
}

export function databaseTableOptions(
  tables: { value: string; label: string; children: unknown[] }[]
): DatabaseTableOption[] {
  return tables.map(table => ({
    value: table.value,
    label: table.label,
    children: (table.children ?? []).flatMap(
      (child: unknown): DatabaseSheetOption[] => {
        if (
          typeof child !== 'object' ||
          child === null ||
          !('value' in child) ||
          (typeof child.value !== 'string' &&
            typeof child.value !== 'number') ||
          !('label' in child) ||
          typeof child.label !== 'string'
        )
          return [];
        return [{ id: child.value, value: child.label, label: child.label }];
      }
    ),
  }));
}
