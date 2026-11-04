import { useEffect, useRef, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Drawer,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import { v4 as uuid } from 'uuid';
import type { Environment, TenantRole } from '@/services/platform-tenants';
import { listModels, type ModelConnection } from '@/services/platform-models';
import {
  listSkillReleases,
  type SkillRelease,
} from '@/services/platform-skills';
import {
  agentApiURL,
  cancelRun,
  createDeployment,
  disableDeployment,
  getDeployment,
  getRun,
  issueDeploymentKey,
  listDeploymentKeys,
  listDeployments,
  listRuns,
  revokeDeploymentKey,
  rotateDeploymentKey,
  submitRun,
  subscribeRun,
  type AgentInput,
  type AgentRun,
  type Deployment,
  type DeploymentKey,
  type RunEvent,
  type RunState,
} from '@/services/platform-agents';
import './agents.css';

const terminal = (state: RunState): boolean =>
  ['succeeded', 'failed', 'cancelled', 'timed_out'].includes(state);
const labels: Record<RunState, string> = {
  queued: '排队中',
  running: '运行中',
  cancel_requested: '正在取消',
  cancelled: '已取消',
  succeeded: '已完成',
  failed: '失败',
  timed_out: '超时',
};
const report = (): void => {
  message.error('操作未完成，请检查当前权限、环境配置或服务状态。');
};

function RunConsole({
  deployment,
  onClose,
}: {
  deployment: Deployment;
  onClose: () => void;
}): JSX.Element {
  const [metadata, setMetadata] = useState<Deployment>();
  const [rows, setRows] = useState<AgentRun[]>([]);
  const [selected, setSelected] = useState<string>();
  const [run, setRun] = useState<AgentRun>();
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [connection, setConnection] = useState(0);
  const mounted = useRef(true);
  const pending = useRef<{ text: string; key: string }>();
  useEffect(() => {
    mounted.current = true;
    void getDeployment(deployment.deployment_id)
      .then(async value => {
        if (!mounted.current) return;
        setMetadata(value);
        if (value.allowed_actions?.read) {
          const items = await listRuns(value.deployment_id);
          if (mounted.current) setRows(items);
        }
      })
      .catch(() => {
        if (mounted.current)
          setError('无法读取部署权限，请重新打开或联系管理员。');
      });
    return () => {
      mounted.current = false;
    };
  }, [deployment.deployment_id]);
  useEffect(() => {
    setRun(undefined);
    setEvents([]);
    setError('');
    if (!selected) return;
    const controller = new AbortController();
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const refresh = async (): Promise<void> => {
      try {
        const state = await getRun(selected);
        if (stopped) return;
        setRun(state);
        setRows(previous =>
          [state, ...previous.filter(item => item.run_id !== selected)].slice(
            0,
            50
          )
        );
        if (!terminal(state.state))
          timer = setTimeout(() => {
            void refresh();
          }, 2500);
      } catch {
        if (!stopped)
          setError(
            '任务状态暂时不可用。任务不会因页面断线而取消，可重新连接查询。'
          );
      }
    };
    void refresh();
    void subscribeRun(
      selected,
      controller.signal,
      event => {
        if (stopped) return;
        setEvents(previous =>
          previous.some(item => item.sequence_no === event.sequence_no)
            ? previous
            : [...previous, event].sort((a, b) => a.sequence_no - b.sequence_no)
        );
      },
      () => {
        if (timer) clearTimeout(timer);
        void refresh();
      }
    ).catch((failure: unknown) => {
      if (!stopped)
        setError(
          failure instanceof Error
            ? failure.message
            : '事件订阅中断，可重新连接。'
        );
    });
    return () => {
      stopped = true;
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, [selected, connection]);
  const send = async (): Promise<void> => {
    if (busy || !text.trim()) return;
    const content = text.trim();
    if (pending.current?.text !== content)
      pending.current = { text: content, key: uuid() };
    setBusy(true);
    setError('');
    try {
      const accepted = await submitRun(
        deployment.deployment_id,
        content,
        pending.current.key
      );
      if (!mounted.current) return;
      setSelected(accepted.run_id);
      setText('');
      pending.current = undefined;
    } catch {
      if (mounted.current)
        setError(
          '提交未确认。请保留原消息重试；同一请求键不会重复执行。执行服务未就绪时会拒绝新任务。'
        );
    } finally {
      if (mounted.current) setBusy(false);
    }
  };
  const cancel = async (): Promise<void> => {
    if (!selected) return;
    setBusy(true);
    try {
      const value = await cancelRun(selected);
      if (mounted.current) setRun(value);
    } catch {
      if (mounted.current) report();
    } finally {
      if (mounted.current) setBusy(false);
    }
  };
  const output = events
    .filter(event => event.event_type === 'message.delta')
    .map(event => event.payload.text ?? '')
    .join('');
  const active = run && !terminal(run.state);
  return (
    <Drawer
      open
      width={780}
      title={`${deployment.display_name} · 聊天调试`}
      onClose={onClose}
      destroyOnClose
    >
      <div className="oa-run-console">
        <Space wrap>
          <Tag color="green">
            {deployment.environment === 'test' ? '测试环境' : '生产环境'}
          </Tag>
          <Tag>{deployment.agent_version}</Tag>
          <Tag>DeepAgents</Tag>
        </Space>
        {metadata && !metadata.admission_enabled && (
          <Alert
            type="warning"
            showIcon
            message="执行服务尚未启用"
            description="部署配置和历史记录可查看；运行服务就绪后才能提交真实任务。"
          />
        )}
        {error && (
          <Alert
            type="error"
            showIcon
            message={error}
            action={
              selected ? (
                <Button
                  size="small"
                  onClick={() => setConnection(value => value + 1)}
                >
                  重新连接
                </Button>
              ) : undefined
            }
          />
        )}
        <Space wrap>
          <Select
            aria-label="任务历史"
            placeholder="选择历史任务"
            style={{ minWidth: 340 }}
            value={selected}
            onChange={setSelected}
            options={rows.map(item => ({
              value: item.run_id,
              label: `${labels[item.state]} · ${item.run_id.slice(0, 8)} · ${item.created_at}`,
            }))}
          />
          <Button
            onClick={() => {
              setSelected(undefined);
              setText('');
              pending.current = undefined;
            }}
          >
            新建任务
          </Button>
        </Space>
        {selected && (
          <Card size="small" title="任务状态">
            <Typography.Paragraph copyable>{selected}</Typography.Paragraph>
            {run ? (
              <Space wrap>
                <Tag
                  color={
                    run.state === 'succeeded'
                      ? 'green'
                      : active
                        ? 'blue'
                        : 'default'
                  }
                >
                  {labels[run.state]}
                </Tag>
                <span>版本 {run.agent_version}</span>
                <span>用量 {run.usage?.total_tokens ?? '未报告'} tokens</span>
              </Space>
            ) : (
              <Spin size="small" />
            )}
            {run?.error_code && (
              <Alert type="error" message={`任务未成功：${run.error_code}`} />
            )}
            <Typography.Paragraph type="secondary">
              关闭窗口不会取消任务；重新打开后可从历史记录继续查看。
            </Typography.Paragraph>
          </Card>
        )}
        <div className="oa-run-answer" aria-live="polite">
          {output ||
            (selected
              ? '等待任务输出…'
              : '输入任务后，由当前部署绑定的模型与 Skills 处理。')}
        </div>
        <details>
          <summary>执行事件（{events.length}）</summary>
          <div className="oa-run-events">
            {events
              .filter(event => event.event_type !== 'message.delta')
              .map(event => (
                <div key={event.sequence_no}>
                  <code>
                    #{event.sequence_no} {event.event_type}
                  </code>
                  <span>
                    {event.payload.phase ??
                      event.payload.name ??
                      event.payload.state ??
                      ''}
                  </span>
                </div>
              ))}
          </div>
        </details>
        <Input.TextArea
          aria-label="任务消息"
          value={text}
          onChange={event => setText(event.target.value)}
          maxLength={32768}
          autoSize={{ minRows: 3, maxRows: 8 }}
          placeholder="描述需要 Agent 完成的任务…"
        />
        <Space>
          <Button
            type="primary"
            loading={busy}
            disabled={
              !metadata?.admission_enabled ||
              !metadata.allowed_actions?.create ||
              metadata.state === 'disabled' ||
              !text.trim()
            }
            onClick={() => {
              void send();
            }}
          >
            提交任务
          </Button>
          {active && (
            <Button
              danger
              disabled={busy || !metadata?.allowed_actions?.cancel}
              onClick={() => {
                void cancel();
              }}
            >
              取消当前任务
            </Button>
          )}
        </Space>
      </div>
    </Drawer>
  );
}

function CredentialPanel({
  deployment,
  tenantId,
  onClose,
}: {
  deployment: Deployment;
  tenantId: string;
  onClose: () => void;
}): JSX.Element {
  const [keys, setKeys] = useState<DeploymentKey[]>([]);
  const [token, setToken] = useState('');
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const mounted = useRef(true);
  const refresh = async (): Promise<void> => {
    const values = await listDeploymentKeys(
      tenantId,
      deployment.environment,
      deployment.deployment_id
    );
    if (mounted.current) setKeys(values);
  };
  useEffect(() => {
    mounted.current = true;
    void refresh().catch(report);
    return () => {
      mounted.current = false;
    };
  }, []);
  const action = async (
    operation: () => Promise<{ token: string } | void>
  ): Promise<void> => {
    setBusy(true);
    setToken('');
    try {
      const value = await operation();
      if (mounted.current) {
        setToken(value?.token ?? '');
        await refresh();
      }
    } catch {
      if (mounted.current) report();
    } finally {
      if (mounted.current) setBusy(false);
    }
  };
  return (
    <Modal
      open
      width={760}
      title={`${deployment.display_name} · HTTP 凭据`}
      onCancel={onClose}
      footer={<Button onClick={onClose}>关闭</Button>}
    >
      <Alert
        type="info"
        message="凭据限定到当前租户、环境和部署，读取与取消仅限同一凭据主体的任务。"
      />
      <Space style={{ margin: '18px 0' }}>
        <Input
          placeholder="凭据名称"
          maxLength={128}
          value={name}
          onChange={event => setName(event.target.value)}
        />
        <Button
          disabled={!name.trim()}
          loading={busy}
          onClick={() => {
            void action(() =>
              issueDeploymentKey(
                tenantId,
                deployment.environment,
                deployment.deployment_id,
                name.trim()
              )
            );
          }}
        >
          签发 30 天凭据
        </Button>
      </Space>
      {token && (
        <Alert
          type="warning"
          message="请现在保存，关闭后无法再次查看"
          description={
            <Input.TextArea
              aria-label="新签发凭据"
              readOnly
              value={token}
              autoSize
            />
          }
        />
      )}
      <Table
        size="small"
        rowKey="key_id"
        dataSource={keys}
        pagination={{ pageSize: 5 }}
        columns={[
          { title: '名称', dataIndex: 'display_name' },
          { title: '到期时间', dataIndex: 'expires_at' },
          {
            title: '状态',
            render: (_, row) =>
              row.revoked
                ? '已吊销'
                : new Date(row.expires_at).getTime() <= Date.now()
                  ? '已过期'
                  : '有效',
          },
          {
            title: '操作',
            render: (_, row) => (
              <Space>
                <Button
                  size="small"
                  disabled={busy || row.revoked}
                  onClick={() => {
                    void action(() =>
                      rotateDeploymentKey(
                        tenantId,
                        deployment.environment,
                        deployment.deployment_id,
                        row.key_id
                      )
                    );
                  }}
                >
                  轮换
                </Button>
                <Popconfirm
                  title="吊销后使用此凭据的调用将被拒绝"
                  onConfirm={() =>
                    action(() =>
                      revokeDeploymentKey(
                        tenantId,
                        deployment.environment,
                        deployment.deployment_id,
                        row.key_id
                      )
                    )
                  }
                >
                  <Button size="small" danger disabled={busy || row.revoked}>
                    吊销
                  </Button>
                </Popconfirm>
              </Space>
            ),
          },
        ]}
      />
    </Modal>
  );
}

export default function AgentDeployments({
  tenantId,
  environment,
  role,
  active,
}: {
  tenantId: string;
  environment: Environment;
  role: TenantRole;
  active: boolean;
}): JSX.Element {
  const [rows, setRows] = useState<Deployment[]>([]);
  const [models, setModels] = useState<ModelConnection[]>([]);
  const [skills, setSkills] = useState<SkillRelease[]>([]);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [revision, setRevision] = useState(0);
  const [search, setSearch] = useState('');
  const [create, setCreate] = useState(false);
  const [saving, setSaving] = useState(false);
  const [selected, setSelected] = useState<Deployment>();
  const [credential, setCredential] = useState<Deployment>();
  const [api, setApi] = useState<Deployment>();
  const [form] = Form.useForm<AgentInput>();
  const mounted = useRef(true);
  const canCreate =
    active &&
    environment === 'test' &&
    ['tenant_admin', 'developer'].includes(role);
  const canDisable =
    active &&
    (environment === 'test'
      ? ['tenant_admin', 'developer'].includes(role)
      : role === 'operator');
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  useEffect(() => {
    let ignore = false;
    setLoading(true);
    setFailed(false);
    void Promise.all([
      listDeployments(tenantId, environment),
      listModels(tenantId, environment),
      listSkillReleases(tenantId, environment),
    ])
      .then(([deployments, connections, releases]) => {
        if (!ignore) {
          setRows(deployments);
          setModels(connections.filter(value => !value.revoked));
          setSkills(releases.filter(value => value.status === 'ready'));
        }
      })
      .catch(() => {
        if (!ignore) setFailed(true);
      })
      .finally(() => {
        if (!ignore) setLoading(false);
      });
    return () => {
      ignore = true;
    };
  }, [tenantId, environment, revision]);
  const save = async (input: AgentInput): Promise<void> => {
    const model = models.find(
      item => item.connection_id === input.modelConnectionId
    );
    if (!model) return;
    setSaving(true);
    try {
      await createDeployment(tenantId, {
        ...input,
        modelVersion: model.current_version,
      });
      if (mounted.current) {
        setCreate(false);
        setRevision(value => value + 1);
        message.success('Agent 部署已创建，模型与 Skill 版本已固定。');
      }
    } catch {
      if (mounted.current) report();
    } finally {
      if (mounted.current) setSaving(false);
    }
  };
  const disable = async (row: Deployment): Promise<void> => {
    try {
      await disableDeployment(tenantId, environment, row.deployment_id);
      if (mounted.current) setRevision(value => value + 1);
    } catch {
      if (mounted.current) report();
    }
  };
  const filtered = rows.filter(row =>
    `${row.display_name} ${row.agent_version}`
      .toLowerCase()
      .includes(search.toLowerCase())
  );
  return (
    <div className="oa-agent-workspace">
      <header>
        <div>
          <span className="oa-workspace-kicker">
            OPEN AGENT / {environment.toUpperCase()}
          </span>
          <h2>Agent 工作台</h2>
          <p>固定版本，独立任务，同一套 HTTP 接口与聊天调试。</p>
        </div>
        <Space>
          <Button onClick={() => setRevision(value => value + 1)}>刷新</Button>
          <Button
            type="primary"
            disabled={!canCreate}
            onClick={() => {
              form.resetFields();
              setCreate(true);
            }}
          >
            创建 Agent
          </Button>
        </Space>
      </header>
      <Input.Search
        aria-label="搜索 Agent"
        placeholder="搜索 Agent 名称、版本"
        value={search}
        onChange={event => setSearch(event.target.value)}
        style={{ maxWidth: 380, marginBottom: 20 }}
      />
      {failed && (
        <Alert
          type="error"
          showIcon
          message="当前环境数据不可用，请刷新或检查权限。"
        />
      )}
      {loading ? (
        <Spin />
      ) : (
        !failed &&
        (filtered.length ? (
          <div className="oa-agent-cards">
            {filtered.map(row => (
              <Card
                key={row.deployment_id}
                title={row.display_name}
                extra={
                  <Tag color={row.state === 'disabled' ? 'default' : 'green'}>
                    {row.state === 'published'
                      ? '已发布'
                      : row.state === 'disabled'
                        ? '已停用'
                        : '测试中'}
                  </Tag>
                }
              >
                <Space wrap>
                  <Tag>{row.agent_version}</Tag>
                  <Tag>DeepAgents</Tag>
                  <span>模型配置 v{row.model_version}</span>
                </Space>
                <Typography.Paragraph className="oa-deployment-id" copyable>
                  {row.deployment_id}
                </Typography.Paragraph>
                <Space wrap>
                  <Button type="primary" onClick={() => setSelected(row)}>
                    调试与任务
                  </Button>
                  <Button onClick={() => setApi(row)}>HTTP 接口</Button>
                  {role === 'tenant_admin' && (
                    <Button onClick={() => setCredential(row)}>凭据</Button>
                  )}
                  {canDisable && row.state !== 'disabled' && (
                    <Popconfirm
                      title="停用后拒绝新任务，历史记录仍保留"
                      onConfirm={() => disable(row)}
                    >
                      <Button danger>停用</Button>
                    </Popconfirm>
                  )}
                </Space>
              </Card>
            ))}
          </div>
        ) : (
          <Empty description="当前环境尚无 Agent 部署" />
        ))
      )}
      <Modal
        open={create}
        title="创建 Agent"
        width={680}
        onCancel={() => setCreate(false)}
        onOk={() => form.submit()}
        confirmLoading={saving}
        destroyOnClose
      >
        <Form
          form={form}
          layout="vertical"
          preserve={false}
          initialValues={{
            agentVersion: 'v1.0.0',
            skillReleaseIds: [],
            execution: { maxLoops: 10, temperature: 0.2, maxTokens: 4096 },
          }}
          onFinish={input => {
            void save(input);
          }}
        >
          <Form.Item
            name="displayName"
            label="Agent 名称"
            rules={[{ required: true, whitespace: true, max: 255 }]}
          >
            <Input maxLength={255} />
          </Form.Item>
          <Form.Item
            name="agentVersion"
            label="发布版本"
            rules={[{ required: true, pattern: /^[A-Za-z0-9_.-]{1,128}$/ }]}
          >
            <Input />
          </Form.Item>
          <Form.Item
            name="modelConnectionId"
            label="模型接口"
            rules={[{ required: true }]}
          >
            <Select
              options={models.map(model => ({
                value: model.connection_id,
                label: `${model.display_name} · ${model.model_name} · v${model.current_version}`,
              }))}
            />
          </Form.Item>
          <Form.Item
            name={['execution', 'instructions']}
            label="Agent 指令"
            rules={[{ required: true, whitespace: true, max: 32768 }]}
          >
            <Input.TextArea rows={5} maxLength={32768} />
          </Form.Item>
          <Form.Item name="skillReleaseIds" label="绑定 Skill 版本（可选）">
            <Select
              mode="multiple"
              options={skills.map(skill => ({
                value: skill.release_id,
                label: `${skill.skill_name} · ${skill.version_label}`,
              }))}
            />
          </Form.Item>
          <Space align="start">
            <Form.Item name={['execution', 'maxLoops']} label="最多执行轮次">
              <InputNumber min={1} max={30} />
            </Form.Item>
            <Form.Item name={['execution', 'temperature']} label="温度">
              <InputNumber min={0} max={2} step={0.1} />
            </Form.Item>
            <Form.Item
              name={['execution', 'maxTokens']}
              label="最大输出 tokens"
            >
              <InputNumber min={1} max={16384} />
            </Form.Item>
          </Space>
          <Alert
            type="info"
            message="创建后绑定不可变版本。需要调整配置时创建新版本；生产发布须先通过测试运行。"
          />
        </Form>
      </Modal>
      {selected && (
        <RunConsole
          key={selected.deployment_id}
          deployment={selected}
          onClose={() => setSelected(undefined)}
        />
      )}
      {credential && (
        <CredentialPanel
          key={credential.deployment_id}
          tenantId={tenantId}
          deployment={credential}
          onClose={() => setCredential(undefined)}
        />
      )}
      <Modal
        open={!!api}
        title="部署 HTTP 接口"
        onCancel={() => setApi(undefined)}
        footer={<Button onClick={() => setApi(undefined)}>关闭</Button>}
      >
        {api && (
          <>
            <Typography.Paragraph copyable>
              {agentApiURL(api.deployment_id)}
            </Typography.Paragraph>
            <Typography.Paragraph>
              POST 请求使用部署凭据或用户 Bearer JWT，并携带唯一的
              Idempotency-Key。聊天调试使用同一个任务接口。
            </Typography.Paragraph>
            <pre className="oa-api-example">
              {JSON.stringify(
                {
                  input: { message: '请处理任务', history: [] },
                  timeoutSeconds: 300,
                },
                null,
                2
              )}
            </pre>
          </>
        )}
      </Modal>
    </div>
  );
}
