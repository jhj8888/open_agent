import { useEffect, useState } from 'react';
import { ApiScope, aborted, errorMessage } from './api';
import { ErrorBox, dateText, terminal } from './components';
import type { AgentRun } from './types';

type Output = {
  output_id: string;
  file_name: string;
  source_path: string;
  size_bytes: number;
  sha256: string;
  state: 'pending' | 'available' | 'failed';
  attempt_no: number;
  step_id: string;
  created_at: string;
};

export function TaskOutputs({
  api,
  run,
}: {
  api: ApiScope;
  run: AgentRun;
}): JSX.Element {
  const [files, setFiles] = useState<Output[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');
  const [preview, setPreview] = useState<{ name: string; text: string }>();
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    setFiles([]);
    setError('');
    setPreview(undefined);
    const refresh = async (): Promise<void> => {
      try {
        const rows = await api.request<Output[]>(
          `/api/v1/runs/${run.run_id}/outputs`,
          'GET',
          undefined,
          { signal: controller.signal }
        );
        if (controller.signal.aborted) return;
        setFiles(rows);
        setError('');
      } catch (failure) {
        if (!controller.signal.aborted) setError(errorMessage(failure));
      }
      if (!controller.signal.aborted && !terminal(run.state))
        timer = setTimeout(() => {
          void refresh();
        }, 4000);
    };
    void refresh();
    return () => {
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, [api, run.run_id, run.state]);
  const act = async (file: Output, show: boolean): Promise<void> => {
    if (busy) return;
    setBusy(file.output_id);
    setError('');
    try {
      if (show) {
        const data = await api.request<{ text: string }>(
          `/api/v1/runs/${run.run_id}/outputs/${file.output_id}/preview`
        );
        setPreview({ name: file.file_name, text: data.text });
      } else
        await api.downloadOutput(run.run_id, file.output_id, file.file_name);
    } catch (failure) {
      if (!aborted(failure)) setError(errorMessage(failure));
    } finally {
      setBusy('');
    }
  };
  const partial = [
    'failed',
    'timed_out',
    'cancelled',
    'cancel_requested',
  ].includes(run.state);
  return (
    <section className="task-outputs" aria-label="任务产物">
      <div className="output-heading">
        <strong>任务产物</strong>
        <span>{files.length} 个文件</span>
      </div>
      <p className="form-note">
        已归档文件随任务保留，沙盒回收后仍可下载。
        {partial && ' 本任务未完整完成，以下为已保存的部分产物。'}
      </p>
      <ErrorBox message={error} />
      {!files.length && (
        <p className="form-note">
          {terminal(run.state)
            ? '本次任务没有已登记的文件产物。'
            : '文件生成后将在这里显示。'}
        </p>
      )}
      {files.map(file => (
        <div className="output-file" key={file.output_id}>
          <div className="output-description">
            <strong>{file.file_name}</strong>
            <span>
              {file.size_bytes < 1024
                ? `${file.size_bytes} B`
                : `${(file.size_bytes / 1024).toFixed(1)} KiB`}{' '}
              · {dateText(file.created_at)}
            </span>
            <span>
              步骤 {file.step_id} · 执行尝试 {file.attempt_no}
            </span>
            <details>
              <summary>文件信息</summary>
              <code>
                {file.source_path}
                <br />
                SHA256: {file.sha256}
              </code>
            </details>
          </div>
          {file.state === 'available' ? (
            <div className="output-actions">
              {file.size_bytes <= 256 * 1024 &&
                /\.(txt|md|csv|json|log|ya?ml)$/i.test(file.file_name) && (
                  <button
                    className="link"
                    disabled={!!busy}
                    onClick={() => {
                      void act(file, true);
                    }}
                  >
                    预览
                  </button>
                )}
              <button
                className="secondary"
                disabled={!!busy}
                onClick={() => {
                  void act(file, false);
                }}
              >
                {busy === file.output_id ? '读取中…' : '下载'}
              </button>
            </div>
          ) : (
            <span className="form-note">
              {file.state === 'failed'
                ? '归档失败，文件不可下载'
                : terminal(run.state)
                  ? '归档未完成，文件不可下载'
                  : '正在归档…'}
            </span>
          )}
        </div>
      ))}
      {preview && (
        <div className="output-preview">
          <div className="output-heading">
            <strong>{preview.name}</strong>
            <button className="link" onClick={() => setPreview(undefined)}>
              关闭预览
            </button>
          </div>
          <pre>{preview.text}</pre>
        </div>
      )}
    </section>
  );
}
