import type {
  WorkflowModel,
  WorkflowNodeData,
  WorkflowNodeParameters,
} from '@/components/workflow/types/domain';
import type { ChangeNodeParameter } from '@/components/workflow/nodes/types';
import type {
  ModelSetting,
  ModelSettingValue,
} from '@/components/workflow/nodes/model-settings';
import {
  readModelSettings,
  readModelSetting,
  writeModelSetting,
  numericConstraint,
} from '@/components/workflow/nodes/model-settings';

interface ModelParametersContext {
  item: ModelSetting;
  nodeParam: WorkflowNodeParameters;
  currentSelectModel?: WorkflowModel;
  handleChangeNodeParam: ChangeNodeParameter;
  handleDifferentModel: (
    data: WorkflowNodeData,
    item: ModelSetting,
    value: ModelSettingValue
  ) => void;
  setShowModelParmas: (show: boolean) => void;
}
type ModelParametersProps<Key extends keyof ModelParametersContext> = Pick<
  ModelParametersContext,
  Key
>;

import React, { useEffect, useState, useRef, memo } from 'react';
import { Tooltip, Slider, Switch } from 'antd';
import { useMemoizedFn } from 'ahooks';
import { FlowInputNumber } from '@/components/workflow/ui';
import { useTranslation } from 'react-i18next';

import debuggerIcon from '@/assets/imgs/workflow/debugger-icon.png';
import close from '@/assets/imgs/workflow/modal-close.png';
import questionMark from '@/assets/imgs/common/questionmark.png';

// ----------------- hooks -----------------
function useClickOutside(
  ref: React.RefObject<HTMLDivElement>,
  onClose: () => void
): void | (() => void) {
  useEffect(() => {
    function handleClick(e: MouseEvent): void {
      if (
        ref.current &&
        e.target instanceof Node &&
        !ref.current.contains(e.target)
      ) {
        onClose();
      }
    }
    document.body.addEventListener('click', handleClick);
    return (): void => document.body.removeEventListener('click', handleClick);
  }, [ref, onClose]);
}

function useConfigs(
  currentSelectModel: WorkflowModel | undefined,
  setConfigs: React.Dispatch<React.SetStateAction<ModelSetting[]>>
): void {
  useEffect(() => {
    setConfigs(readModelSettings(currentSelectModel));
  }, [currentSelectModel, setConfigs]);
}

// ----------------- 子组件 -----------------
function ParamSwitch({
  item,
  nodeParam,
  currentSelectModel,
  handleChangeNodeParam,
  handleDifferentModel,
}: ModelParametersProps<
  | 'item'
  | 'nodeParam'
  | 'currentSelectModel'
  | 'handleChangeNodeParam'
  | 'handleDifferentModel'
>): React.ReactElement {
  return (
    <Switch
      className="list-switch config-switch"
      checked={
        currentSelectModel?.llmSource === 0
          ? Boolean(nodeParam.extraParams?.[item.key])
          : !readModelSetting(nodeParam, item.key)
      }
      onChange={val =>
        handleChangeNodeParam(
          (data, v) => handleDifferentModel(data, item, v),
          currentSelectModel?.llmSource === 0 ? val : !val
        )
      }
    />
  );
}

function ParamRange({
  item,
  nodeParam,
  handleChangeNodeParam,
  handleDifferentModel,
}: ModelParametersProps<
  'item' | 'nodeParam' | 'handleChangeNodeParam' | 'handleDifferentModel'
>): React.ReactElement {
  const configured =
    readModelSetting(nodeParam, item.key) ?? nodeParam.extraParams?.[item.key];
  const value = typeof configured === 'number' ? configured : undefined;

  return (
    <div className="w-full flex items-center justify-between">
      <Slider
        min={numericConstraint(item.constraintContent[0]?.name)}
        max={numericConstraint(item.constraintContent[1]?.name)}
        step={item?.precision || 1}
        value={value}
        className="flex-1 config-slider nodrag"
        onChange={val =>
          handleChangeNodeParam(
            (data, v) => handleDifferentModel(data, item, v),
            val
          )
        }
      />
      <FlowInputNumber
        className="global-inputnumber-center ml-[18px] pt-1.5 pl-0.5 w-[60px] text-center nodrag"
        value={value}
        onChange={val =>
          handleChangeNodeParam(
            (data, v) => handleDifferentModel(data, item, v),
            val
          )
        }
        onBlur={() => {
          if (
            nodeParam?.extraParams &&
            nodeParam?.extraParams?.[item.key] === null
          ) {
            handleChangeNodeParam(data => {
              data.nodeParam.extraParams = {
                ...data.nodeParam.extraParams,
                [item.key]: item.default,
              };
            }, item.default);
          }
          if (readModelSetting(nodeParam, item.key) === null) {
            handleChangeNodeParam(
              data => writeModelSetting(data.nodeParam, item.key, item.default),
              item.default
            );
          }
        }}
        step={item?.precision || 1}
        min={numericConstraint(item.constraintContent[0]?.name)}
        max={numericConstraint(item.constraintContent[1]?.name)}
        controls={false}
      />
    </div>
  );
}

function ParamItem({
  item,
  nodeParam,
  currentSelectModel,
  handleChangeNodeParam,
  handleDifferentModel,
}: ModelParametersProps<
  | 'item'
  | 'nodeParam'
  | 'currentSelectModel'
  | 'handleChangeNodeParam'
  | 'handleDifferentModel'
>): React.ReactElement {
  return (
    <div>
      <div className="flex items-center gap-1 justify-between">
        <div className="flex items-center gap-1">
          <span>{item.name}</span>
          {item.desc && (
            <Tooltip
              title={item.desc}
              overlayClassName="black-tooltip config-secret"
            >
              <img src={questionMark} width={16} className="ml-1" alt="" />
            </Tooltip>
          )}
        </div>
        {item.constraintType === 'switch' && (
          <ParamSwitch
            {...{
              item,
              nodeParam,
              currentSelectModel,
              handleChangeNodeParam,
              handleDifferentModel,
            }}
          />
        )}
      </div>
      {item.constraintType === 'range' && (
        <ParamRange
          {...{ item, nodeParam, handleChangeNodeParam, handleDifferentModel }}
        />
      )}
    </div>
  );
}

// ----------------- 主组件 -----------------
function ModelParams({
  setShowModelParmas,
  currentSelectModel,
  nodeParam,
  handleChangeNodeParam,
}: ModelParametersProps<
  | 'setShowModelParmas'
  | 'currentSelectModel'
  | 'nodeParam'
  | 'handleChangeNodeParam'
>): React.ReactElement {
  const { t } = useTranslation();
  const paramsRef = useRef<HTMLDivElement | null>(null);
  const [configs, setConfigs] = useState<ModelSetting[]>([]);

  useConfigs(currentSelectModel, setConfigs);
  useClickOutside(paramsRef, () => setShowModelParmas(false));

  const handleDifferentModel = useMemoizedFn(
    (data: WorkflowNodeData, item: ModelSetting, value: ModelSettingValue) => {
      if (currentSelectModel?.llmSource === 0) {
        Reflect.deleteProperty(data.nodeParam, item.key);
        data.nodeParam.extraParams = {
          ...data.nodeParam.extraParams,
          [item.key]: value,
        };
      } else {
        writeModelSetting(data.nodeParam, item.key, value);
      }
    }
  );

  return (
    <div
      ref={paramsRef}
      className="absolute right-[-3px] top-8 border border-[#f5f7fc] bg-[#fff] rounded-lg p-4"
      style={{
        zIndex: 100,
        width: 'calc(100% - 4px)',
        boxShadow: '0px 4px 10px 0px rgba(0, 0, 0, 0.3)',
      }}
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <img src={debuggerIcon} className="w-3 h-3" alt="" />
          <span className="font-medium text-base">
            {t('workflow.nodes.modelSelect.modelParamsSettings')}
          </span>
        </div>
        <img
          src={close}
          className="w-3 h-3 cursor-pointer"
          alt=""
          onClick={e => {
            e.stopPropagation();
            setShowModelParmas(false);
          }}
        />
      </div>
      <div className="flex flex-col gap-2 w-full text-second font-medium mt-4">
        {configs
          ?.filter(item => ['range', 'switch'].includes(item.constraintType))
          ?.map((item, index) => (
            <ParamItem
              key={index}
              {...{
                item,
                nodeParam,
                currentSelectModel,
                handleChangeNodeParam,
                handleDifferentModel,
              }}
            />
          ))}
      </div>
    </div>
  );
}

export default memo(ModelParams);
