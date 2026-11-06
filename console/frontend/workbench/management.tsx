import React, { useEffect, useRef, useState } from 'react';
import { ApiScope, aborted, errorMessage } from './api';
import {
  dateText,
  Empty,
  ErrorBox,
  Icon,
  Modal,
  Status,
  useResource,
} from './components';
import type {
  AgentRun,
  AuditEntry,
  Deployment,
  DeploymentKey,
  Member,
  ModelConnection,
  ProbeResult,
  Quotas,
  SkillManifest,
  SkillRelease,
  Tenant,
} from './types';

const text = (data: FormData, name: string): string =>
  String(data.get(name) ?? '').trim();
const number = (data: FormData, name: string): number => Number(data.get(name));
type EditorProps = { api: ApiScope; close: () => void; saved: () => void };

export function EditForm({
  title,
  close,
  action,
  saved,
  children,
  submit = '保存',
  wide = false,
}: React.PropsWithChildren<{
  title: string;
  close: () => void;
  action: (data: FormData) => Promise<unknown>;
  saved: () => void;
  submit?: string;
  wide?: boolean;
}>): JSX.Element {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const alive = useRef(true);
  const submitting = useRef(false);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  return (
    <Modal title={title} wide={wide} onClose={close}>
      <form
        onSubmit={event => {
          event.preventDefault();
          if (submitting.current) return;
          const data = new FormData(event.currentTarget);
          submitting.current = true;
          setBusy(true);
          setError('');
          void (async () => {
            try {
              // Local validation may throw before returning an API promise.
              await action(data);
              if (alive.current) {
                saved();
                close();
              }
            } catch (failure) {
              if (alive.current && !aborted(failure))
                setError(errorMessage(failure));
            } finally {
              submitting.current = false;
              if (alive.current) setBusy(false);
            }
          })();
        }}
      >
        <div className="form-body">
          <ErrorBox message={error} />
          {children}
        </div>
        <footer>
          <span className="small muted">
            {busy ? '正在提交，请稍候…' : '操作将保存到当前环境'}
          </span>
          <button type="button" className="button" onClick={close}>
            关闭
          </button>
          <button className="button primary" disabled={busy}>
            {submit}
          </button>
        </footer>
      </form>
    </Modal>
  );
}

export function AgentEditor({
  api,
  close,
  saved,
  models,
  skills,
}: EditorProps & {
  models: ModelConnection[];
  skills: SkillRelease[];
}): JSX.Element {
  return (
    <EditForm
      title="创建 Agent"
      close={close}
      saved={saved}
      submit="创建测试部署"
      wide
      action={data => {
        const model = models.find(
          item => item.connection_id === text(data, 'model')
        );
        if (!model) throw new Error('请选择有效的模型连接。');
        return api.mutate('agent-deployments', {
          displayName: text(data, 'name'),
          agentVersion: text(data, 'version'),
          modelConnectionId: model.connection_id,
          modelVersion: model.current_version,
          skillReleaseIds: data.getAll('skill'),
          execution: {
            instructions: text(data, 'instructions'),
            maxLoops: number(data, 'loops'),
            maxTokens: number(data, 'tokens'),
            temperature: 0.2,
          },
        });
      }}
    >
      <div className="form-row">
        <label>
          Agent 名称
          <input
            name="name"
            required
            maxLength={120}
            placeholder="例如：报表分析助手"
          />
        </label>
        <label>
          版本
          <input name="version" required maxLength={64} defaultValue="1.0.0" />
        </label>
      </div>
      <label>
        模型连接
        <select name="model" required defaultValue="">
          <option value="" disabled>
            选择模型与固定版本
          </option>
          {models
            .filter(item => !item.revoked)
            .map(item => (
              <option key={item.connection_id} value={item.connection_id}>
                {item.display_name} · {item.model_name} · v
                {item.current_version}
              </option>
            ))}
        </select>
      </label>
      <label>
        Agent 指令
        <textarea
          name="instructions"
          required
          rows={5}
          maxLength={20000}
          placeholder="描述 Agent 的职责、处理流程与输出要求。"
        />
      </label>
      <label>绑定 Skill 版本</label>
      <div className="skill-picker">
        {skills
          .filter(item => item.status === 'ready')
          .map(item => (
            <label key={item.release_id}>
              <input name="skill" type="checkbox" value={item.release_id} />
              <span>
                {item.skill_name}
                <small>{item.version_label}</small>
              </span>
            </label>
          ))}
      </div>
      {!skills.length && (
        <p className="form-note">
          暂未上传 Skill。可先创建仅调用模型的 Agent。
        </p>
      )}
      <div className="form-row">
        <label>
          最大执行循环
          <input
            name="loops"
            type="number"
            min={1}
            max={30}
            defaultValue={20}
            required
          />
        </label>
        <label>
          最大输出 Token
          <input
            name="tokens"
            type="number"
            min={1}
            max={16384}
            defaultValue={4096}
            required
          />
        </label>
      </div>
      <p className="form-note">
        部署固定模型与 Skill
        版本。后续修改配置需创建新版本；通过测试后可发布到生产。
      </p>
    </EditForm>
  );
}

export function ModelEditor({
  api,
  close,
  saved,
  model,
}: EditorProps & { model?: ModelConnection }): JSX.Element {
  const [adapter, setAdapter] = useState(model?.adapter ?? 'openai');
  return (
    <EditForm
      title={model ? '更新模型连接版本' : '添加模型连接'}
      close={close}
      saved={saved}
      action={data =>
        api.mutate(
          `models${model ? `/${model.connection_id}` : ''}`,
          {
            displayName: text(data, 'name'),
            endpoint: text(data, 'endpoint'),
            modelName: text(data, 'model'),
            adapter,
            thinkingEnabled: adapter === 'deepseek' && data.has('thinking'),
            apiKey: text(data, 'key') || undefined,
            supportsStream: data.has('stream'),
            supportsTools: data.has('tools'),
            supportsUsage: data.has('usage'),
            connectTimeoutSeconds: number(data, 'connect'),
            readTimeoutSeconds: number(data, 'read'),
            totalTimeoutSeconds: number(data, 'total'),
            caRef: text(data, 'ca') || undefined,
            expectedVersion: model?.current_version ?? 0,
            revokePrevious: data.has('revoke'),
          },
          model ? 'PUT' : 'POST'
        )
      }
    >
      <label>
        连接名称
        <input
          name="name"
          required
          maxLength={120}
          defaultValue={model?.display_name}
        />
      </label>
      <label>
        API Base URL
        <input
          name="endpoint"
          type="url"
          required
          maxLength={2048}
          defaultValue={model?.endpoint}
          placeholder="https://model.internal/v1"
        />
      </label>
      <label>
        模型名称
        <input
          name="model"
          required
          maxLength={255}
          defaultValue={model?.model_name}
        />
      </label>
      <label>
        API Key{model && '（留空保留现有密钥）'}
        <input
          name="key"
          type="password"
          autoComplete="new-password"
          maxLength={4096}
          required={!model}
        />
      </label>
      <label>
        模型接口类型
        <select
          value={adapter}
          onChange={event =>
            setAdapter(event.target.value as 'openai' | 'deepseek')
          }
        >
          <option value="openai">OpenAI 兼容</option>
          <option value="deepseek">DeepSeek（支持 think）</option>
        </select>
      </label>
      {adapter === 'deepseek' && (
        <label className="checkbox-row">
          <input
            name="thinking"
            type="checkbox"
            defaultChecked={model?.thinking_enabled ?? true}
          />
          启用模型思考（步骤详情可查看模型返回的 think）
        </label>
      )}
      <div className="checkbox-row">
        <label>
          <input
            name="stream"
            type="checkbox"
            defaultChecked={model?.supports_stream ?? true}
          />
          流式输出
        </label>
        <label>
          <input
            name="tools"
            type="checkbox"
            defaultChecked={model?.supports_tools ?? true}
          />
          工具调用
        </label>
        <label>
          <input
            name="usage"
            type="checkbox"
            defaultChecked={model?.supports_usage ?? true}
          />
          用量统计
        </label>
      </div>
      <div className="form-row">
        <label>
          连接超时（秒）
          <input
            name="connect"
            type="number"
            min={1}
            max={60}
            defaultValue={model?.connect_timeout_seconds ?? 15}
            required
          />
        </label>
        <label>
          读取超时（秒）
          <input
            name="read"
            type="number"
            min={1}
            max={1800}
            defaultValue={model?.read_timeout_seconds ?? 120}
            required
          />
        </label>
      </div>
      <div className="form-row">
        <label>
          总超时（秒）
          <input
            name="total"
            type="number"
            min={1}
            max={3600}
            defaultValue={model?.total_timeout_seconds ?? 300}
            required
          />
        </label>
        <label>
          内部 CA 引用（选填）
          <input name="ca" maxLength={128} defaultValue={model?.ca_ref ?? ''} />
        </label>
      </div>
      {model && (
        <label className="checkbox-row">
          <input type="checkbox" name="revoke" />
          撤销旧版本凭据（会影响绑定旧版本的部署）
        </label>
      )}
      <p className="form-note">
        支持 OpenAI
        兼容协议。模型地址需在平台配置的网络白名单中；密钥加密保存，不会再次返回页面。
      </p>
    </EditForm>
  );
}

export function ModelsPage({
  api,
  models,
  canEdit,
  canProbe,
  refresh,
}: {
  api: ApiScope;
  models: ModelConnection[];
  canEdit: boolean;
  canProbe: boolean;
  refresh: () => void;
}): JSX.Element {
  const [edit, setEdit] = useState<ModelConnection | 'new'>();
  const [probing, setProbing] = useState('');
  const [result, setResult] = useState('');
  const [error, setError] = useState('');
  const probe = async (id: string): Promise<void> => {
    setProbing(id);
    setError('');
    setResult('');
    try {
      const value = await api.mutate<ProbeResult>(`models/${id}/probe`, {});
      setResult(
        value.ok
          ? `连接验证通过 · ${value.durationMs} ms · 工具调用${value.toolsVerified ? '已验证' : '未验证'} · 用量${value.usage === 'reported' ? '已报告' : '未报告'}`
          : `验证未通过：${value.error ?? '模型接口未就绪'}`
      );
    } catch (failure) {
      if (!aborted(failure)) setError(errorMessage(failure));
    } finally {
      setProbing('');
    }
  };
  return (
    <>
      <div className="section-toolbar">
        <span className="muted">当前环境 · {models.length} 个模型连接</span>
        <button
          className="button primary"
          disabled={!canEdit}
          onClick={() => setEdit('new')}
        >
          <Icon name="plus" size={16} />
          添加模型
        </button>
      </div>
      <ErrorBox message={error} />
      {result && <div className="info-strip">{result}</div>}
      <div className="agent-grid">
        {models.map(item => (
          <article className="agent-card" key={item.connection_id}>
            <div className="agent-card-top">
              <span className="agent-icon blue">
                <Icon name="spark" />
              </span>
              <span className="version">v{item.current_version}</span>
            </div>
            <h2>{item.display_name}</h2>
            <p>{item.model_name}</p>
            <div className="model-endpoint mono">{item.endpoint}</div>
            <div className="skill-tags">
              {item.supports_stream && <span>流式输出</span>}
              {item.supports_tools && <span>工具调用</span>}
              {item.supports_usage && <span>Token 用量</span>}
            </div>
            <footer>
              <button
                className="link"
                disabled={!!probing || !canProbe}
                onClick={() => {
                  void probe(item.connection_id);
                }}
              >
                {probing === item.connection_id ? '验证中…' : '验证连接'}
              </button>
              <button
                className="link"
                disabled={!canEdit}
                onClick={() => setEdit(item)}
              >
                编辑 <Icon name="chevron" size={14} />
              </button>
            </footer>
          </article>
        ))}
      </div>
      {!models.length && (
        <Empty
          text="还没有模型连接"
          detail="配置模型 HTTP 接口后即可创建 Agent。"
        />
      )}
      {edit && (
        <ModelEditor
          api={api}
          model={edit === 'new' ? undefined : edit}
          close={() => setEdit(undefined)}
          saved={refresh}
        />
      )}
    </>
  );
}

export function SkillUploader({ api, close, saved }: EditorProps): JSX.Element {
  const images = useResource(
    () => api.list<string[]>('skill-releases/runtime-images'),
    [api]
  );
  return (
    <EditForm
      title="上传 Skill 版本"
      close={close}
      saved={saved}
      submit="校验并发布"
      action={data => {
        const archive = data.get('archive');
        if (
          !(archive instanceof File) ||
          !archive.size ||
          archive.size > 8 * 1024 * 1024
        )
          throw new Error('请选择不超过 8 MiB 的 ZIP 文件。');
        return api.mutate('skill-releases', data);
      }}
    >
      <ErrorBox message={images.error} retry={images.refresh} />
      <label>
        Skill ZIP 文件
        <input
          name="archive"
          type="file"
          accept=".zip,application/zip"
          required
        />
      </label>
      <label>
        版本号
        <input name="version" defaultValue="1.0.0" required maxLength={64} />
      </label>
      <label>
        沙盒运行镜像
        <select name="runtimeImage" required defaultValue="">
          <option value="" disabled>
            选择已批准的镜像
          </option>
          {images.data?.map(item => (
            <option value={item} key={item}>
              {item}
            </option>
          ))}
        </select>
      </label>
      <p className="form-note">
        ZIP 根目录包含 SKILL.md，可包含 scripts/、references/、assets/
        和依赖锁文件。平台校验文件路径和清单，并固定镜像摘要。上传不会执行包内代码。
      </p>
    </EditForm>
  );
}

export function SkillViewer({
  api,
  skill,
  close,
}: {
  api: ApiScope;
  skill: SkillRelease;
  close: () => void;
}): JSX.Element {
  const manifest = useResource(
    async () =>
      JSON.parse(
        await api.file(`skill-releases/${skill.release_id}/manifest`)
      ) as SkillManifest,
    [api, skill.release_id]
  );
  const [index, setIndex] = useState(0);
  useEffect(() => {
    const found = manifest.data?.files.findIndex(
      item => item.path === 'SKILL.md'
    );
    if (found !== undefined && found >= 0) setIndex(found);
  }, [manifest.data]);
  const file = useResource(
    () => api.file(`skill-releases/${skill.release_id}/files/${index}`),
    [api, skill.release_id, index]
  );
  return (
    <Modal
      title={`${skill.skill_name} · ${skill.version_label}`}
      onClose={close}
      wide
    >
      <div className="form-body">
        <p>{skill.description}</p>
        <div className="detail-grid">
          <div>
            <span>文件数量</span>
            <b>{skill.file_count}</b>
          </div>
          <div>
            <span>体积</span>
            <b>{(skill.total_bytes / 1024).toFixed(1)} KiB</b>
          </div>
          <div>
            <span>状态</span>
            <b>{skill.verified ? '已验证' : '待验证'}</b>
          </div>
        </div>
        <ErrorBox message={manifest.error || file.error} />
        <div className="package-layout">
          <div className="file-tree">
            <b>资源文件</b>
            {manifest.data?.files.map((item, position) => (
              <button
                key={item.path}
                className={position === index ? 'selected' : ''}
                onClick={() => setIndex(position)}
              >
                {item.path}
              </button>
            ))}
          </div>
          <pre className="skill-content">
            {file.loading ? '正在读取…' : file.data?.slice(0, 150000)}
          </pre>
        </div>
        <p className="form-note mono">清单 SHA-256：{skill.manifest_sha256}</p>
        <p className="form-note mono">镜像：{skill.runtime_image}</p>
      </div>
    </Modal>
  );
}

export function Credentials({
  api,
  deployment,
  close,
  canManage,
}: {
  api: ApiScope;
  deployment: Deployment;
  close: () => void;
  canManage: boolean;
}): JSX.Element {
  const resource = `agent-deployments/${deployment.deployment_id}/credentials`;
  const keys = useResource(
    () =>
      canManage ? api.list<DeploymentKey[]>(resource) : Promise.resolve([]),
    [api, resource, canManage]
  );
  const grantResource = `agent-deployments/${deployment.deployment_id}/grants`;
  const grants = useResource(
    () =>
      canManage && api.environment === 'prod'
        ? api.list<{ uid: string; created_at: string }[]>(grantResource)
        : Promise.resolve([]),
    [api, grantResource, canManage]
  );
  const members = useResource(
    () =>
      canManage && api.environment === 'prod'
        ? api.list<Member[]>('members')
        : Promise.resolve([]),
    [api, canManage]
  );
  const [grantUid, setGrantUid] = useState('');
  const [secret, setSecret] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [name, setName] = useState('');
  const modify = async (
    operation: () => Promise<{ token?: string } | undefined>
  ): Promise<void> => {
    setBusy(true);
    setError('');
    setSecret('');
    try {
      const result = await operation();
      setSecret(result?.token ?? '');
      keys.refresh();
      grants.refresh();
    } catch (failure) {
      if (!aborted(failure)) setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  };
  const example = `curl -X POST '${location.origin}/api/v1/agent-deployments/${deployment.deployment_id}/runs' \\\n  -H 'Authorization: Bearer <DEPLOYMENT_KEY>' \\\n  -H 'Content-Type: application/json' \\\n  -H 'Idempotency-Key: <UNIQUE_REQUEST_ID>' \\\n  -d '{"input":{"message":"你好"},"timeoutSeconds":300}'`;
  return (
    <Modal
      title={`${deployment.display_name} · HTTP 接口`}
      onClose={close}
      wide
    >
      <div className="form-body">
        <div className="info-strip">
          <span className="method">POST</span>
          <code>/api/v1/agent-deployments/{deployment.deployment_id}/runs</code>
        </div>
        <pre className="code-block">{example}</pre>
        <p className="form-note">
          提交返回 202 和 run_id。GET /api/v1/runs/{'{run_id}'}{' '}
          查询状态；/events 订阅事件；POST /cancel
          取消。任务仍由当前部署的租户与环境约束。
        </p>
        <ErrorBox
          message={error || keys.error || grants.error || members.error}
        />
        {secret && (
          <div className="secret-reveal">
            <b>新凭据仅显示一次</b>
            <p>关闭窗口后无法再次查看，请保存到调用方的密钥配置中。</p>
            <textarea readOnly aria-label="新部署凭据" value={secret} />
            <button
              className="button"
              onClick={() => {
                void navigator.clipboard
                  .writeText(secret)
                  .catch(() => setError('复制失败，请手动选择复制。'));
              }}
            >
              复制凭据
            </button>
          </div>
        )}
        {canManage && (
          <form
            className="inline-form"
            onSubmit={event => {
              event.preventDefault();
              void modify(() =>
                api.mutate(resource, {
                  displayName: name.trim(),
                  canCreate: true,
                  canRead: true,
                  canCancel: true,
                  validDays: 30,
                })
              );
            }}
          >
            <input
              aria-label="凭据名称"
              placeholder="凭据名称"
              required
              maxLength={120}
              value={name}
              onChange={event => setName(event.target.value)}
            />
            <button className="button primary" disabled={busy || !name.trim()}>
              签发 30 天凭据
            </button>
          </form>
        )}
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>名称</th>
                <th>到期时间</th>
                <th>状态</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {keys.data?.map(key => (
                <tr key={key.key_id}>
                  <td>
                    {key.display_name}
                    <span className="subline mono">
                      {key.key_id.slice(0, 8)}
                    </span>
                  </td>
                  <td>{dateText(key.expires_at)}</td>
                  <td>{key.revoked ? '已撤销' : '有效'}</td>
                  <td>
                    <button
                      className="link"
                      disabled={busy || key.revoked}
                      onClick={() => {
                        void modify(() =>
                          api.mutate(`${resource}/${key.key_id}/rotate`)
                        );
                      }}
                    >
                      轮换
                    </button>
                    <button
                      className="link danger"
                      disabled={busy || key.revoked}
                      onClick={() => {
                        void modify(() =>
                          api.mutate(
                            `${resource}/${key.key_id}`,
                            undefined,
                            'DELETE'
                          )
                        );
                      }}
                    >
                      撤销
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {canManage && api.environment === 'prod' && (
          <section className="production-grants">
            <h3>生产聊天执行授权</h3>
            <p className="form-note">
              生产角色不自动包含执行权限。为当前部署显式授权后，成员才可从聊天窗口发起任务；撤销后立即停止该授权。
            </p>
            <form
              className="inline-form"
              onSubmit={event => {
                event.preventDefault();
                if (grantUid)
                  void modify(() =>
                    api.mutate(
                      `${grantResource}/${encodeURIComponent(grantUid)}`,
                      undefined,
                      'PUT'
                    )
                  );
              }}
            >
              <select
                aria-label="生产执行成员"
                required
                value={grantUid}
                onChange={event => setGrantUid(event.target.value)}
              >
                <option value="">选择当前环境成员</option>
                {members.data
                  ?.filter(
                    member =>
                      !grants.data?.some(grant => grant.uid === member.uid)
                  )
                  .map(member => (
                    <option key={member.uid} value={member.uid}>
                      {member.username} · {member.role}
                    </option>
                  ))}
              </select>
              <button className="button primary" disabled={busy || !grantUid}>
                授予执行权限
              </button>
            </form>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>成员 UID</th>
                    <th>授权时间</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {grants.data?.map(grant => (
                    <tr key={grant.uid}>
                      <td>{grant.uid}</td>
                      <td>{dateText(grant.created_at)}</td>
                      <td>
                        <button
                          className="link danger"
                          disabled={busy}
                          onClick={() => {
                            void modify(() =>
                              api.mutate(
                                `${grantResource}/${encodeURIComponent(grant.uid)}`,
                                undefined,
                                'DELETE'
                              )
                            );
                          }}
                        >
                          撤销执行权限
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}
        {!canManage && (
          <p className="form-note">
            当前角色可查看接口说明，签发与撤销凭据需要对应管理权限。
          </p>
        )}
      </div>
    </Modal>
  );
}

export function Promotion({ api, close, saved }: EditorProps): JSX.Element {
  const source = useResource(
    () =>
      api.request<Deployment[]>(
        `/platform/tenants/${encodeURIComponent(api.tenant)}/environments/test/agent-deployments`
      ),
    [api]
  );
  const models = useResource(
    () => api.list<ModelConnection[]>('models'),
    [api]
  );
  const [deployment, setDeployment] = useState('');
  const runs = useResource(
    () =>
      deployment
        ? api.request<AgentRun[]>(
            `/api/v1/agent-deployments/${deployment}/runs`
          )
        : Promise.resolve([]),
    [api, deployment]
  );
  return (
    <EditForm
      title="从测试环境发布"
      close={close}
      saved={saved}
      submit="发布到生产"
      action={data => {
        const model = models.data?.find(
          item => item.connection_id === text(data, 'model')
        );
        if (!model) throw new Error('请选择生产模型连接。');
        return api.mutate('agent-deployments/promote', {
          sourceDeploymentId: deployment,
          verificationRunId: text(data, 'run'),
          modelConnectionId: model.connection_id,
          modelVersion: model.current_version,
        });
      }}
    >
      <ErrorBox message={source.error || models.error || runs.error} />
      <label>
        测试部署
        <select
          required
          value={deployment}
          onChange={event => setDeployment(event.target.value)}
        >
          <option value="">选择部署</option>
          {source.data
            ?.filter(item => item.state !== 'disabled')
            .map(item => (
              <option key={item.deployment_id} value={item.deployment_id}>
                {item.display_name} · {item.agent_version}
              </option>
            ))}
        </select>
      </label>
      <label>
        成功测试记录
        <select required name="run" key={deployment} defaultValue="">
          <option value="">选择验证任务</option>
          {runs.data
            ?.filter(item => item.state === 'succeeded')
            .map(item => (
              <option key={item.run_id} value={item.run_id}>
                {item.run_id.slice(0, 8)} · {dateText(item.finished_at)}
              </option>
            ))}
        </select>
      </label>
      <label>
        生产模型连接
        <select name="model" required defaultValue="">
          <option value="">选择模型</option>
          {models.data
            ?.filter(item => !item.revoked)
            .map(item => (
              <option key={item.connection_id} value={item.connection_id}>
                {item.display_name} · v{item.current_version}
              </option>
            ))}
        </select>
      </label>
      <p className="form-note">
        发布人需要生产环境运维角色。服务端校验指定测试任务成功、资源回收和相同
        Skill 摘要，一并推广已验证包并创建生产部署。发布不会覆盖历史部署。
      </p>
    </EditForm>
  );
}

export function TenantPage({
  api,
  tenant,
  platformAdmin,
  admin,
  changed,
}: {
  api: ApiScope;
  tenant: Tenant;
  platformAdmin: boolean;
  admin: boolean;
  changed: () => void;
}): JSX.Element {
  const quotas = useResource(() => api.list<Quotas>('quotas'), [api]);
  const members = useResource(
    () => (admin ? api.list<Member[]>('members') : Promise.resolve([])),
    [api, admin]
  );
  const audit = useResource(() => api.list<AuditEntry[]>('audit'), [api]);
  const [edit, setEdit] = useState<
    'quota' | 'tenant' | 'member' | 'administrator'
  >();
  const [member, setMember] = useState<Member>();
  const [error, setError] = useState('');
  const refresh = (): void => {
    quotas.refresh();
    members.refresh();
    audit.refresh();
    changed();
  };
  const statusChange = async (status: Tenant['status']): Promise<void> => {
    try {
      await api.request(
        `/platform/tenants/${encodeURIComponent(api.tenant)}/status`,
        'PUT',
        { status, revision: tenant.revision }
      );
      changed();
    } catch (failure) {
      if (!aborted(failure)) setError(errorMessage(failure));
    }
  };
  return (
    <>
      <div className="section-toolbar">
        <div>
          <Status state={tenant.status} />
          <span className="muted"> · {tenant.tenantId}</span>
        </div>
        <div className="row-actions">
          {platformAdmin && (
            <>
              <button
                className="button"
                disabled={tenant.status !== 'active'}
                onClick={() => setEdit('administrator')}
              >
                登记环境管理员
              </button>
              <button
                className="button"
                onClick={() => {
                  void statusChange(
                    tenant.status === 'active' ? 'suspended' : 'active'
                  );
                }}
              >
                {tenant.status === 'active' ? '暂停租户' : '恢复租户'}
              </button>
              <button
                className="button primary"
                onClick={() => setEdit('tenant')}
              >
                <Icon name="plus" size={16} />
                创建租户
              </button>
            </>
          )}
        </div>
      </div>
      <ErrorBox message={error} />
      <div className="panel">
        <div className="panel-heading">
          <h2>环境配额</h2>
          <button
            className="link"
            disabled={!admin || !quotas.data}
            onClick={() => setEdit('quota')}
          >
            调整配额
          </button>
        </div>
        <ErrorBox message={quotas.error} />
        {quotas.data && (
          <div className="quota-grid">
            {Object.entries({
              并发任务: quotas.data.maxConcurrentRuns,
              排队任务: quotas.data.maxQueuedRuns,
              并发沙盒: quotas.data.maxSandboxes,
              '每日 Token': quotas.data.dailyTokenBudget,
              审计保留天数: quotas.data.auditRetentionDays,
            }).map(([label, value]) => (
              <div key={label}>
                <span>{label}</span>
                <strong>{value.toLocaleString()}</strong>
              </div>
            ))}
          </div>
        )}
      </div>
      <div className="panel recent">
        <div className="panel-heading">
          <h2>成员与角色</h2>
          <button
            className="link"
            disabled={!admin}
            onClick={() => {
              setMember(undefined);
              setEdit('member');
            }}
          >
            添加成员
          </button>
        </div>
        <ErrorBox message={members.error} />
        {admin ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>成员</th>
                  <th>角色</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {members.data?.map(item => (
                  <tr key={item.uid}>
                    <td>
                      {item.username}
                      <span className="subline mono">{item.uid}</span>
                    </td>
                    <td>{item.role}</td>
                    <td>
                      <button
                        className="link"
                        onClick={() => {
                          setMember(item);
                          setEdit('member');
                        }}
                      >
                        编辑
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty
            text="成员管理需要管理员权限"
            detail="当前环境的角色与权限由租户管理员维护。"
          />
        )}
      </div>
      <div className="panel recent">
        <div className="panel-heading">
          <h2>最近审计记录</h2>
          <button className="link" onClick={audit.refresh}>
            刷新
          </button>
        </div>
        <ErrorBox message={audit.error} />
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>操作</th>
                <th>操作人</th>
                <th>对象</th>
                <th>时间</th>
              </tr>
            </thead>
            <tbody>
              {audit.data?.map(item => (
                <tr key={item.id}>
                  <td>{item.action}</td>
                  <td className="mono small">{item.actorUid}</td>
                  <td className="mono small">{item.subjectId}</td>
                  <td>{dateText(item.createdAt)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      {edit === 'tenant' && (
        <EditForm
          title="创建租户"
          close={() => setEdit(undefined)}
          saved={changed}
          action={data =>
            api.request('/platform/tenants', 'POST', {
              displayName: text(data, 'name'),
              ownerUid: text(data, 'owner'),
            })
          }
        >
          <label>
            租户名称
            <input name="name" required maxLength={50} />
          </label>
          <label>
            负责人用户 UID
            <input name="owner" required maxLength={128} />
          </label>
          <p className="form-note">
            负责人需要先登录平台。创建后获得测试环境管理员权限，生产环境单独授权。
          </p>
        </EditForm>
      )}
      {edit === 'administrator' && (
        <EditForm
          title="登记当前环境管理员"
          close={() => setEdit(undefined)}
          saved={refresh}
          action={data =>
            api.mutate(
              `administrators/${encodeURIComponent(text(data, 'uid'))}`,
              {
                role: 'tenant_admin',
                revision: text(data, 'revision')
                  ? number(data, 'revision')
                  : null,
              },
              'PUT'
            )
          }
        >
          <label>
            用户 UID
            <input name="uid" required maxLength={128} />
          </label>
          <label>
            已有成员版本（新成员留空）
            <input name="revision" type="number" min={0} />
          </label>
          <p className="form-note">
            仅平台管理员可执行。将指定用户登记为
            {api.environment === 'test' ? '测试' : '生产'}
            环境管理员，不自动授予生产发布或执行权限。
          </p>
        </EditForm>
      )}
      {edit === 'quota' && quotas.data && (
        <EditForm
          title="调整环境配额"
          close={() => setEdit(undefined)}
          saved={refresh}
          action={data =>
            api.mutate(
              'quotas',
              {
                maxConcurrentRuns: number(data, 'runs'),
                maxQueuedRuns: number(data, 'queue'),
                maxSandboxes: number(data, 'sandboxes'),
                dailyTokenBudget: number(data, 'tokens'),
                auditRetentionDays: number(data, 'retention'),
                revision: quotas.data!.revision,
              },
              'PUT'
            )
          }
        >
          <label>
            并发任务
            <input
              name="runs"
              required
              type="number"
              min={0}
              max={100000}
              defaultValue={quotas.data.maxConcurrentRuns}
            />
          </label>
          <label>
            排队任务
            <input
              name="queue"
              required
              type="number"
              min={0}
              max={1000000}
              defaultValue={quotas.data.maxQueuedRuns}
            />
          </label>
          <label>
            并发沙盒
            <input
              name="sandboxes"
              required
              type="number"
              min={0}
              max={100000}
              defaultValue={quotas.data.maxSandboxes}
            />
          </label>
          <label>
            每日 Token 预算
            <input
              name="tokens"
              required
              type="number"
              min={0}
              defaultValue={quotas.data.dailyTokenBudget}
            />
          </label>
          <label>
            审计保留天数
            <input
              name="retention"
              required
              type="number"
              min={1}
              max={3650}
              defaultValue={quotas.data.auditRetentionDays}
            />
          </label>
        </EditForm>
      )}
      {edit === 'member' && (
        <EditForm
          title={member ? '更新成员权限' : '添加成员'}
          close={() => setEdit(undefined)}
          saved={refresh}
          action={data => {
            const uid = encodeURIComponent(text(data, 'uid'));
            const remove = data.has('remove');
            return api.mutate(
              `members/${uid}${remove ? `?revision=${member?.revision}` : ''}`,
              remove
                ? undefined
                : {
                    role: text(data, 'role'),
                    revision: member?.revision ?? null,
                  },
              remove ? 'DELETE' : 'PUT'
            );
          }}
        >
          <label>
            用户 UID
            <input
              name="uid"
              required
              maxLength={128}
              defaultValue={member?.uid}
              readOnly={!!member}
            />
          </label>
          <label>
            角色
            <select name="role" defaultValue={member?.role ?? 'developer'}>
              <option value="tenant_admin">租户管理员</option>
              <option value="developer">开发者</option>
              <option value="operator">运维人员</option>
              <option value="viewer">只读成员</option>
            </select>
          </label>
          {member && (
            <label className="checkbox-row">
              <input name="remove" type="checkbox" />
              移除此环境的成员权限
            </label>
          )}
          <p className="form-note">
            用户须已存在，最后一位管理员不能被移除。测试与生产权限分别管理。
          </p>
        </EditForm>
      )}
    </>
  );
}
