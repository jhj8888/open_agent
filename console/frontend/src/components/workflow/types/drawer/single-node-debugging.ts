import type { RefInput } from './chat-debugger';

export interface UseSingleNodeDebuggingReturn {
  handleRun: () => void;
  handleChangeParam: <Value>(
    index: number,
    fn: (data: RefInput, value: Value) => void,
    value: Value
  ) => void;
  uploadComplete: (
    event: ProgressEvent<EventTarget>,
    index: number,
    fileId: string
  ) => void;
  handleFileUpload: (
    file: File,
    index: number,
    multiple: boolean,
    fileId: string
  ) => void;
  handleDeleteFile: (index: number, fileId: string) => void;
  canRunDebugger: boolean;
}
