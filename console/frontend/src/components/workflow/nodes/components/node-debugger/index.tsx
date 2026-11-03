import type {
  ChatDebuggerNodeData,
  NodeDebuggerResult,
} from '@/components/workflow/types/drawer/chat-debugger';
import React, { useState, useMemo, useRef, useEffect, memo } from 'react';
import JSONPretty from 'react-json-view';
import { cloneDeep } from 'lodash';
import useFlowsManager from '@/components/workflow/store/use-flows-manager';
import { message } from 'antd';
import { InfoCircleOutlined } from '@ant-design/icons';
import copy from 'copy-to-clipboard';
import MarkdownRender from '@/components/markdown-render';
import i18next from 'i18next';
import { useMemoizedFn } from 'ahooks';
import { Icons } from '@/components/workflow/icons';

type CopyData = (text: string | undefined) => void;
type ResultProps<Key extends keyof NodeDebuggerResult> = Pick<
  NodeDebuggerResult,
  Key
> & {
  copyData: CopyData;
};

interface DebuggingStatusProps {
  id: string;
  status: ChatDebuggerNodeData['status'];
  debuggerResult?: NodeDebuggerResult;
  openResultModal?: boolean;
}

interface DebuggingResultProps {
  setShowModal: (show: boolean) => void;
  debuggerResult?: NodeDebuggerResult;
}

const NodeDebuggingStatusNoMemo = ({
  id,
  status,
  debuggerResult,
  openResultModal = false,
}: DebuggingStatusProps): React.ReactElement => {
  const getCurrentStore = useFlowsManager(state => state.getCurrentStore);
  const currentStore = getCurrentStore();
  const setNodes = currentStore(state => state.setNodes);
  const [showModal, setShowModal] = useState(false);

  useEffect(() => {
    openResultModal && setShowModal(true);
  }, [openResultModal]);

  const style = useMemo(() => {
    return {
      backgroundBg:
        status === 'running'
          ? Icons.nodeDebugger.nodeOperationRunningBg
          : status === 'cancel'
            ? Icons.nodeDebugger.nodeOperationCancelBg
            : status === 'success'
              ? Icons.nodeDebugger.nodeOperationSuccessBg
              : Icons.nodeDebugger.nodeOperationFailBg,
      icon:
        status === 'running'
          ? Icons.nodeDebugger.nodeOperationRunning
          : status === 'cancel'
            ? Icons.nodeDebugger.nodeOperationCancel
            : status === 'success'
              ? Icons.nodeDebugger.nodeOperationSuccess
              : Icons.nodeDebugger.nodeOperationFailed,
      color:
        status === 'running'
          ? '#FFF'
          : status === 'cancel'
            ? '#FF9645'
            : status === 'success'
              ? '#86D2A8'
              : '#FE8585',
    };
  }, [status]);

  const isRunning = useMemo(() => {
    return status === 'running';
  }, [status]);

  return (
    <div
      className="flex items-center justify-between text-xs relative"
      style={{
        boxShadow: 'inset 0px 0px 13px 1px rgba(255,110,110,0.3)',
        color: style.color,
        padding: '11px 10px',
        backgroundImage: `url(${style?.backgroundBg})`,
        backgroundSize: 'cover',
        backgroundRepeat: 'no-repeat',
        borderRadius: '6px 6px 0 0',
        pointerEvents: 'auto',
      }}
    >
      <div className="flex items-center">
        <img
          src={style.icon}
          className={`w-[18px] h-[18px] ${status === 'running' ? 'flow-rotate-center' : ''}`}
          alt=""
        />
        <span className="ml-2.5">
          {status === 'running'
            ? i18next.t('workflow.promptDebugger.running')
            : status === 'cancel'
              ? i18next.t('workflow.promptDebugger.cancel')
              : status === 'success'
                ? i18next.t('workflow.promptDebugger.success')
                : i18next.t('workflow.promptDebugger.failed')}
        </span>
        {!isRunning && debuggerResult?.timeCost !== undefined && (
          <>
            <span
              className="w-[1px] h-[10px] mx-3 mt-0.5"
              style={{
                background: style?.color,
              }}
            ></span>
            <span>
              {i18next.t('workflow.promptDebugger.timeCost')}
              {debuggerResult?.timeCost}s&nbsp;&nbsp;
            </span>
          </>
        )}
        {!isRunning && debuggerResult?.tokenCost !== undefined && (
          <>
            <span>
              {i18next.t('workflow.promptDebugger.totalTokens')}
              {debuggerResult?.tokenCost}tokens
            </span>
          </>
        )}
      </div>
      {!isRunning && (
        <div
          className="flex items-center gap-1.5 cursor-pointer"
          onClick={(e): void => {
            e.stopPropagation();
            setNodes(old =>
              cloneDeep(
                old.map(old => ({
                  ...old,
                  selected: old.id === id ? true : false,
                }))
              )
            );
            setShowModal(!showModal);
          }}
        >
          <img
            src={Icons.nodeDebugger.nodeOperationSuccessArrowRight}
            className="w-[6px] h-[11px] mb-0.5"
            alt=""
          />
        </div>
      )}
      {showModal && (
        <NodeDebuggingResult
          setShowModal={setShowModal}
          debuggerResult={debuggerResult}
        />
      )}
    </div>
  );
};

function ResultBlock({
  title,
  onCopy,
  children,
}: {
  title: string;
  onCopy: () => void;
  children: React.ReactNode;
}): React.ReactElement {
  return (
    <div className="flex flex-col rounded-lg bg-[#F7F7F7]">
      <div
        className="flex items-center justify-between bg-[#EAEDF2] px-4 py-1.5"
        style={{ borderRadius: '8px 8px 0 0' }}
      >
        <span className="font-medium">{title}</span>
        <img
          src={Icons.nodeDebugger.resultCopyIcon}
          className="w-4 h-4 cursor-pointer"
          alt=""
          onClick={onCopy}
        />
      </div>
      {children}
    </div>
  );
}

function InputResult({
  input,
  copyData,
}: ResultProps<'input'>): React.ReactElement | null {
  if (!input || typeof input !== 'object' || Object.keys(input).length === 0)
    return null;
  return (
    <div className="flex flex-col rounded-lg bg-[#F7F7F7]">
      <div
        className="flex items-center justify-between bg-[#EAEDF2] px-4 py-1.5"
        style={{ borderRadius: '8px 8px 0 0' }}
      >
        <div className="flex items-center gap-2">
          <span className="font-medium">
            {i18next.t('workflow.nodes.common.input')}
          </span>
          <span className="text-xs text-[#FF9645]">
            {Object.prototype.hasOwnProperty.call(input, 'chatHistory') ? (
              <div className="flex items-center gap-1">
                <InfoCircleOutlined />
                <span>
                  {i18next.t('workflow.promptDebugger.chatHistoryTokenLimit')}
                </span>
              </div>
            ) : (
              ''
            )}
          </span>
        </div>
        <img
          src={Icons.nodeDebugger.resultCopyIcon}
          className="w-4 h-4 cursor-pointer"
          alt=""
          onClick={() => copyData(JSON.stringify(input))}
        />
      </div>
      <div className="p-4">
        <JSONPretty name={false} src={input} theme="rjv-default" />
      </div>
    </div>
  );
}

function RawOutputResult({
  rawOutput,
  copyData,
}: ResultProps<'rawOutput'>): React.ReactElement | null {
  if (!rawOutput) return null;
  return (
    <ResultBlock
      title={i18next.t('workflow.nodes.flowChatResult.rawOutput')}
      onCopy={() =>
        copyData(
          typeof rawOutput === 'string' ? rawOutput : JSON.stringify(rawOutput)
        )
      }
    >
      <div className="p-4 break-all">
        {typeof rawOutput === 'string' ? rawOutput : JSON.stringify(rawOutput)}
      </div>
    </ResultBlock>
  );
}

function OutputResult({
  output,
  copyData,
}: ResultProps<'output'>): React.ReactElement | null {
  if (!output || typeof output !== 'object' || Object.keys(output).length === 0)
    return null;
  return (
    <ResultBlock
      title={i18next.t('workflow.nodes.common.output')}
      onCopy={() => copyData(JSON.stringify(output))}
    >
      <div className="p-4">
        <JSONPretty name={false} src={output} theme="rjv-default" />
      </div>
    </ResultBlock>
  );
}

function ReasoningContentResult({
  reasoningContent,
  copyData,
}: ResultProps<'reasoningContent'>): React.ReactElement | null {
  if (!reasoningContent) return null;
  return (
    <ResultBlock
      title={i18next.t('workflow.promptDebugger.reasoningContent')}
      onCopy={() => copyData(JSON.stringify(reasoningContent))}
    >
      <div className="bg-[#f7f7f7] p-3.5 small-size-markdown deep-seek-think">
        <MarkdownRender content={reasoningContent} isSending={false} />
      </div>
    </ResultBlock>
  );
}

function AnswerContentResult({
  answerMode,
  answerContent,
  copyData,
}: ResultProps<'answerMode' | 'answerContent'>): React.ReactElement | null {
  if (answerMode !== 1) return null;
  return (
    <ResultBlock
      title={i18next.t('workflow.promptDebugger.answerContent')}
      onCopy={() => copyData(JSON.stringify(answerContent))}
    >
      <div className="bg-[#f7f7f7] p-3.5 small-size-markdown">
        <MarkdownRender content={answerContent ?? ''} isSending={false} />
      </div>
    </ResultBlock>
  );
}

function ErrorOutputsResult({
  errorOutputs,
  copyData,
}: ResultProps<'errorOutputs'>): React.ReactElement | null {
  if (
    !errorOutputs ||
    typeof errorOutputs !== 'object' ||
    Object.keys(errorOutputs).length === 0
  )
    return null;
  return (
    <ResultBlock
      title="错误信息"
      onCopy={() => copyData(JSON.stringify(errorOutputs))}
    >
      <div className="p-4">
        <JSONPretty name={false} src={errorOutputs} theme="rjv-default" />
      </div>
    </ResultBlock>
  );
}

function FailedReasonResult({
  failedReason,
  copyData,
}: ResultProps<'failedReason'>): React.ReactElement | null {
  if (!failedReason) return null;
  return (
    <ResultBlock
      title={i18next.t('workflow.promptDebugger.errorMessage')}
      onCopy={() => copyData(failedReason)}
    >
      <pre className="text-[#F74E43] p-3.5">{failedReason}</pre>
    </ResultBlock>
  );
}

function CancelReasonResult({
  cancelReason,
  copyData,
}: ResultProps<'cancelReason'>): React.ReactElement | null {
  if (!cancelReason) return null;
  return (
    <ResultBlock
      title={i18next.t('workflow.promptDebugger.warning')}
      onCopy={() => copyData(cancelReason)}
    >
      <p className="p-3.5">{cancelReason}</p>
    </ResultBlock>
  );
}

function NodeDebuggingResult({
  setShowModal,
  debuggerResult,
}: DebuggingResultProps): React.ReactElement {
  const consultRef = useRef<HTMLDivElement | null>(null);

  useEffect((): void | (() => void) => {
    const textarea = consultRef.current;
    if (textarea) {
      const handleWheel = (event: WheelEvent): void => {
        event.stopPropagation();
      };
      textarea.addEventListener('wheel', handleWheel);
      return (): void => textarea.removeEventListener('wheel', handleWheel);
    }
  }, []);

  const copyData = useMemoizedFn((data: string | undefined): void => {
    copy(data ?? '');
    message.success(i18next.t('workflow.nodes.flowChatResult.copySuccess'));
  });

  return (
    <div
      className="w-[512px] rounded-lg bg-[#fff] border border-[#f5f7fc] shadow-md p-4 pr-0 absolute right-[-526px] top-[-2px] text-[#000] pointer-events-auto text-base"
      style={{ zIndex: 100 }}
      onClick={e => e.stopPropagation()}
    >
      <div className="flex items-center justify-between pr-4">
        <div className="flex items-center gap-1.5 text-base font-medium">
          <img
            src={Icons.nodeDebugger.debuggerResultIconPng}
            className="w-4 h-4"
            alt=""
          />
          <span>{i18next.t('workflow.promptDebugger.runResult')}</span>
        </div>
        <img
          src={Icons.nodeDebugger.close}
          className="w-3 h-3 cursor-pointer"
          alt=""
          onClick={() => setShowModal(false)}
        />
      </div>

      <div
        ref={consultRef}
        className="flex flex-col gap-2.5 mt-4 overscroll-contain max-h-[550px] overflow-auto pr-4"
      >
        <InputResult input={debuggerResult?.input} copyData={copyData} />
        <RawOutputResult
          rawOutput={debuggerResult?.rawOutput}
          copyData={copyData}
        />
        <OutputResult output={debuggerResult?.output} copyData={copyData} />
        <ReasoningContentResult
          reasoningContent={debuggerResult?.reasoningContent}
          copyData={copyData}
        />
        <AnswerContentResult
          answerMode={debuggerResult?.answerMode}
          answerContent={debuggerResult?.answerContent}
          copyData={copyData}
        />
        <ErrorOutputsResult
          errorOutputs={debuggerResult?.errorOutputs}
          copyData={copyData}
        />
        <FailedReasonResult
          failedReason={debuggerResult?.failedReason}
          copyData={copyData}
        />
        <CancelReasonResult
          cancelReason={debuggerResult?.cancelReason}
          copyData={copyData}
        />
      </div>
    </div>
  );
}

export const NodeDebuggingStatus = memo(NodeDebuggingStatusNoMemo);
