import React, { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import type { User } from 'oidc-client-ts';
import { auth, initializeAuth } from './auth';
import { ApiScope, errorMessage } from './api';
import {
  dateText,
  Empty,
  ErrorBox,
  Icon,
  Status,
  terminal,
  useResource,
} from './components';
import { RunConsole } from './chat';
import {
  AgentEditor,
  Credentials,
  EditForm,
  ModelsPage,
  Promotion,
  SkillUploader,
  SkillViewer,
  TenantPage,
} from './management';
import type {
  AgentRun,
  Deployment,
  Environment,
  Membership,
  ModelConnection,
  SkillRelease,
  Tenant,
} from './types';
import '../preview/style.css';
import './style.css';

type Page =
  | 'agents'
  | 'skills'
  | 'tasks'
  | 'deployments'
  | 'models'
  | 'monitor'
  | 'tenants';
const pages: { id: Page; label: string; icon: string; description: string }[] =
  [
    {
      id: 'agents',
      label: 'Agent 工作台',
      icon: 'grid',
      description: '从 Skill 到 Agent，在一个工作台完成创建、调试与发布。',
    },
    {
      id: 'skills',
      label: 'Skill 资源库',
      icon: 'box',
      description: '管理可版本化的技能包，为 Agent 组合可靠的执行能力。',
    },
    {
      id: 'tasks',
      label: '任务中心',
      icon: 'tasks',
      description: '跟踪每一次执行，查看任务状态、日志与结果。',
    },
    {
      id: 'deployments',
      label: '发布与 API',
      icon: 'api',
      description: '管理环境与发布版本，为每个 Agent 提供独立的调用入口。',
    },
    {
      id: 'models',
      label: '模型连接',
      icon: 'spark',
      description: '连接已有的模型 HTTP 服务，维护版本与调用凭据。',
    },
    {
      id: 'monitor',
      label: '监控概览',
      icon: 'pulse',
      description: '查看当前可见任务的状态、耗时与模型用量。',
    },
    {
      id: 'tenants',
      label: '租户管理',
      icon: 'people',
      description: '让团队拥有独立的成员权限、环境配额与审计记录。',
    },
  ];

function Brand(): JSX.Element {
  return (
    <div className="brand">
      <span className="brand-mark">
        <i />
        <i />
        <i />
        <i />
      </span>
      <span>
        Open Agent<small>企业智能体平台</small>
      </span>
    </div>
  );
}
function Login({ error: initialError = '' }: { error?: string }): JSX.Element {
  const [error, setError] = useState(initialError);
  const [busy, setBusy] = useState(false);
  return (
    <div className="login-shell">
      <section className="login-story">
        <Brand />
        <div>
          <div className="eyebrow">YOUR AGENTS. YOUR WORKSPACE.</div>
          <h1>
            让智能体成为
            <br />
            团队的日常能力。
          </h1>
          <p>
            统一管理 Skill、Agent 与任务，
            <br />
            从测试验证到生产发布，每一步都有迹可循。
          </p>
        </div>
        <span>隔离执行 · 版本管理 · 任务可追踪</span>
      </section>
      <main className="login-panel">
        <div>
          <span className="agent-icon green">
            <Icon name="shield" size={28} />
          </span>
          <h1>进入工作台</h1>
          <p>使用企业统一身份登录，访问获授权的租户与环境。</p>
          <ErrorBox message={error} />
          <button
            className="button primary full"
            disabled={busy}
            onClick={() => {
              setBusy(true);
              void auth.signinRedirect().catch(() => {
                setError('登录服务暂时不可用，请稍后重试。');
                setBusy(false);
              });
            }}
          >
            {busy ? '正在前往登录…' : '企业账号登录'}
            <Icon name="arrow" size={17} />
          </button>
          <span className="login-footnote">Open Agent · 在线工作台</span>
        </div>
      </main>
    </div>
  );
}

function App({ user }: { user: User }): JSX.Element {
  const rootApi = useMemo(() => new ApiScope('', 'test'), []);
  useEffect(() => () => rootApi.dispose(), [rootApi]);
  const tenants = useResource(
    () =>
      rootApi.request<{ platformAdmin: boolean; tenants: Tenant[] }>(
        '/platform/tenants'
      ),
    [rootApi]
  );
  const [tenantId, setTenantId] = useState('');
  const [environment, setEnvironment] = useState<Environment>('test');
  const [page, setPage] = useState<Page>('agents');
  useEffect(() => {
    if (
      tenants.data &&
      !tenants.data.tenants.some(item => item.tenantId === tenantId)
    ) {
      setTenantId(
        (
          tenants.data.tenants.find(
            item => item.scopeKind === 'space' && item.status === 'active'
          ) ?? tenants.data.tenants[0]
        )?.tenantId ?? ''
      );
    }
  }, [tenants.data, tenantId]);
  const tenant = tenants.data?.tenants.find(item => item.tenantId === tenantId);
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <Brand />
        <div className="workspace-label">
          WORKSPACE <span>01</span>
        </div>
        <nav aria-label="主导航">
          {pages.map(item => (
            <button
              key={item.id}
              className={`nav-item ${page === item.id ? 'active' : ''}`}
              onClick={() => setPage(item.id)}
            >
              <Icon name={item.icon} />
              <span>{item.label}</span>
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="private-card">
            <Icon name="shield" />
            <div>
              企业智能体平台<small>隔离执行 · 统一管理</small>
            </div>
          </div>
          <button
            className="profile"
            onClick={() => {
              void auth
                .signoutRedirect({
                  post_logout_redirect_uri: `${location.origin}/open-agent/`,
                })
                .catch(() => {
                  void auth.removeUser().then(() => location.reload());
                });
            }}
          >
            <span className="avatar">OA</span>
            <span>
              {String(
                user.profile.name ??
                  user.profile.preferred_username ??
                  '已登录用户'
              )}
              <small>退出工作台</small>
            </span>
            <Icon name="arrow" size={15} />
          </button>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div className="breadcrumbs">
            <Icon name="grid" size={17} />
            <select
              aria-label="当前租户"
              value={tenantId}
              onChange={event => {
                setTenantId(event.target.value);
                setEnvironment('test');
              }}
            >
              {tenants.data?.tenants.map(item => (
                <option key={item.tenantId} value={item.tenantId}>
                  {item.displayName}
                </option>
              ))}
            </select>
            <span>/</span>
            <span>{pages.find(item => item.id === page)?.label}</span>
          </div>
          <div className="top-actions">
            <span className="live-badge">
              <i />
              在线工作台
            </span>
            <button
              className="icon-button"
              aria-label="刷新租户"
              onClick={tenants.refresh}
            >
              <Icon name="refresh" size={17} />
            </button>
            <span className="small-avatar">OA</span>
          </div>
        </header>
        <ErrorBox message={tenants.error} retry={tenants.refresh} />
        {tenant ? (
          <Workspace
            key={`${tenant.tenantId}:${environment}`}
            tenant={tenant}
            environment={environment}
            setEnvironment={setEnvironment}
            page={page}
            setPage={setPage}
            platformAdmin={tenants.data?.platformAdmin ?? false}
            changed={tenants.refresh}
          />
        ) : (
          <main>
            <Empty
              text={tenants.loading ? '正在加载工作台…' : '暂未分配租户'}
              detail="租户数据由当前登录账号的权限决定。"
            />
          </main>
        )}
      </div>
    </div>
  );
}

type ScopedRun = AgentRun & { deployment_id: string; agentName: string };
function Workspace({
  tenant,
  environment,
  setEnvironment,
  page,
  setPage,
  platformAdmin,
  changed,
}: {
  tenant: Tenant;
  environment: Environment;
  setEnvironment: (value: Environment) => void;
  page: Page;
  setPage: (value: Page) => void;
  platformAdmin: boolean;
  changed: () => void;
}): JSX.Element {
  const api = useMemo(
    () => new ApiScope(tenant.tenantId, environment),
    [tenant.tenantId, environment]
  );
  useEffect(() => () => api.dispose(), [api]);
  const deployments = useResource(
    () => api.list<Deployment[]>('agent-deployments'),
    [api]
  );
  const models = useResource(
    () => api.list<ModelConnection[]>('models'),
    [api]
  );
  const skills = useResource(
    () => api.list<SkillRelease[]>('skill-releases'),
    [api]
  );
  const memberships = useResource(
    () =>
      api.request<Membership[]>(
        `/platform/tenants/${encodeURIComponent(tenant.tenantId)}/memberships`
      ),
    [api]
  );
  const runs = useResource(async () => {
    const items = deployments.data ?? [];
    const rows: ScopedRun[] = [];
    let unavailable = 0;
    for (let index = 0; index < items.length; index += 4) {
      const batch = items.slice(index, index + 4);
      const results = await Promise.allSettled(
        batch.map(item =>
          api.request<AgentRun[]>(
            `/api/v1/agent-deployments/${item.deployment_id}/runs`
          )
        )
      );
      results.forEach((result, offset) => {
        const agent = batch[offset]!;
        if (result.status === 'fulfilled')
          rows.push(
            ...result.value.map(item => ({
              ...item,
              deployment_id: agent.deployment_id,
              agentName: agent.display_name,
            }))
          );
        else unavailable++;
      });
    }
    return {
      rows: rows.sort((a, b) => b.created_at.localeCompare(a.created_at)),
      unavailable,
    };
  }, [api, deployments.data]);
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState('all');
  const [dialog, setDialog] = useState<'agent' | 'upload' | 'promote'>();
  const [chat, setChat] = useState<{ deployment: Deployment; run?: string }>();
  const [credential, setCredential] = useState<Deployment>();
  const [viewSkill, setViewSkill] = useState<SkillRelease>();
  const [disable, setDisable] = useState<Deployment>();
  const [deleteSkill, setDeleteSkill] = useState<SkillRelease>();
  const role = memberships.data?.find(
    item => item.environment === environment
  )?.role;
  const admin = role === 'tenant_admin';
  const activeTenant = tenant.status === 'active';
  const canEdit =
    activeTenant && environment === 'test' && (admin || role === 'developer');
  const canPublish =
    activeTenant && environment === 'prod' && role === 'operator';
  const canCredential = activeTenant && admin;
  const rows = runs.data?.rows ?? [];
  const available = deployments.data ?? [];
  const allSkills = skills.data ?? [];
  const allModels = models.data ?? [];
  const refresh = (): void => {
    deployments.refresh();
    models.refresh();
    skills.refresh();
    memberships.refresh();
  };
  useEffect(() => {
    if (
      !['tasks', 'monitor'].includes(page) &&
      !rows.some(item => !terminal(item.state))
    )
      return;
    const timer = setInterval(runs.refresh, 10000);
    return () => clearInterval(timer);
  }, [page, runs.data]);
  const pageInfo = pages.find(item => item.id === page)!;
  const completed = rows.filter(item => terminal(item.state));
  const succeeded = completed.filter(item => item.state === 'succeeded');
  const knownUsage = rows.filter(item => item.usage?.total_tokens != null);
  const tokenTotal = knownUsage.reduce(
    (sum, item) => sum + (item.usage?.total_tokens ?? 0),
    0
  );
  const duration = (item: AgentRun): string =>
    item.finished_at && item.started_at
      ? `${Math.max(0, (new Date(item.finished_at).getTime() - new Date(item.started_at).getTime()) / 1000).toFixed(1)} s`
      : '—';
  const openRun = (item: ScopedRun): void => {
    const deployment = available.find(
      agent => agent.deployment_id === item.deployment_id
    );
    if (deployment) setChat({ deployment, run: item.run_id });
  };
  const taskTable = (items: ScopedRun[]): JSX.Element => (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>任务 / Agent</th>
            <th>状态</th>
            <th>耗时</th>
            <th>Token</th>
            <th>创建时间</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {items.map(item => (
            <tr key={item.run_id}>
              <td>
                <button className="task-link" onClick={() => openRun(item)}>
                  {item.agentName}
                </button>
                <span className="subline mono">{item.run_id.slice(0, 8)}</span>
              </td>
              <td>
                <Status state={item.state} />
              </td>
              <td>{duration(item)}</td>
              <td>{item.usage?.total_tokens?.toLocaleString() ?? '未报告'}</td>
              <td className="muted">{dateText(item.created_at)}</td>
              <td>
                <button className="link" onClick={() => openRun(item)}>
                  查看详情 <Icon name="chevron" size={14} />
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {!items.length && (
        <Empty
          text={runs.loading ? '正在读取任务…' : '这个范围内还没有任务'}
          detail="在 Agent 卡片中打开聊天调试，发起一次真实任务。"
        />
      )}
    </div>
  );
  return (
    <main>
      <div className="page-heading">
        <div>
          <div className="eyebrow">{tenant.displayName} · OPEN AGENT</div>
          <h1>{pageInfo.label}</h1>
          <p>{pageInfo.description}</p>
        </div>
        <div className="heading-actions">
          <div className="environment-switch" aria-label="切换环境">
            {(['test', 'prod'] as const).map(value => (
              <button
                key={value}
                className={
                  environment === value
                    ? `selected ${value === 'prod' ? 'production' : ''}`
                    : ''
                }
                onClick={() => setEnvironment(value)}
              >
                {value === 'test' ? '测试环境' : '生产环境'}
              </button>
            ))}
          </div>
          {page === 'agents' && (
            <button
              className="button primary"
              disabled={environment === 'test' ? !canEdit : !canPublish}
              onClick={() =>
                setDialog(environment === 'test' ? 'agent' : 'promote')
              }
            >
              <Icon name="plus" size={16} />
              {environment === 'test' ? '创建 Agent' : '发布 Agent'}
            </button>
          )}
        </div>
      </div>
      <ErrorBox message={memberships.error} />
      {!memberships.loading && !role && (
        <div className="info-strip">
          当前账号尚未获得此环境的成员权限。测试与生产环境独立授权。
        </div>
      )}
      {(['agents', 'tasks', 'deployments', 'monitor'] as Page[]).includes(
        page
      ) && <ErrorBox message={deployments.error} retry={deployments.refresh} />}
      {['agents', 'monitor'].includes(page) && (
        <div className="stats-grid">
          {[
            [
              'Agent 部署',
              deployments.data ? String(available.length) : '—',
              '当前租户与环境',
              'grid',
            ],
            [
              '活跃任务',
              runs.data
                ? String(rows.filter(item => !terminal(item.state)).length)
                : '—',
              '最近记录中排队与执行任务',
              'tasks',
            ],
            [
              '任务成功率',
              completed.length
                ? `${((succeeded.length / completed.length) * 100).toFixed(1)}%`
                : '—',
              `${completed.length} 个已结束任务`,
              'chart',
            ],
            [
              '已报告 Token',
              knownUsage.length ? tokenTotal.toLocaleString() : '—',
              `${knownUsage.length} 个任务报告用量`,
              'spark',
            ],
          ].map(([label, value, note, icon]) => (
            <div className="stat-card" key={label}>
              <div>
                <span>{label}</span>
                <Icon name={icon!} size={17} />
              </div>
              <strong>{value}</strong>
              <footer>{note}</footer>
            </div>
          ))}
        </div>
      )}
      {['agents', 'tasks', 'monitor'].includes(page) && (
        <>
          <ErrorBox message={runs.error} retry={runs.refresh} />
          {!!runs.data?.unavailable && (
            <div className="info-strip">
              {runs.data.unavailable}{' '}
              个部署的任务记录不可读取，统计仅包含成功读取的记录。
            </div>
          )}
        </>
      )}
      {page === 'agents' && (
        <>
          <div className="section-toolbar">
            <div className="category-tabs">
              <button className="selected">
                全部 Agent <span>{available.length}</span>
              </button>
            </div>
            <div className="search">
              <Icon name="search" size={17} />
              <input
                aria-label="搜索 Agent"
                placeholder="搜索 Agent 名称、版本…"
                value={search}
                onChange={event => setSearch(event.target.value)}
              />
            </div>
            <button
              className="icon-button"
              aria-label="刷新 Agent"
              onClick={refresh}
            >
              <Icon name="refresh" size={17} />
            </button>
          </div>
          <div className="agent-grid">
            {available
              .filter(item =>
                `${item.display_name} ${item.agent_version}`
                  .toLowerCase()
                  .includes(search.toLowerCase())
              )
              .map((item, index) => (
                <article className="agent-card" key={item.deployment_id}>
                  <div className="agent-card-top">
                    <span
                      className={`agent-icon ${['green', 'blue', 'orange', 'violet'][index % 4]}`}
                    >
                      <Icon name={index % 2 ? 'code' : 'spark'} size={23} />
                    </span>
                    <Status state={item.state} />
                    <button
                      className="icon-button"
                      aria-label={`${item.display_name} 的 API`}
                      onClick={() => setCredential(item)}
                    >
                      <Icon name="api" size={17} />
                    </button>
                  </div>
                  <h2>
                    {item.display_name}
                    <span>{item.agent_version}</span>
                  </h2>
                  <p>
                    固定版本部署 ·{' '}
                    {environment === 'test'
                      ? '在测试环境验证执行效果'
                      : '提供稳定的生产调用入口'}
                  </p>
                  <div className="skill-tags">
                    <span>DeepAgents</span>
                    <span>独立任务</span>
                    <span>HTTP API</span>
                  </div>
                  <div className="agent-model">
                    <span className="model-dot" />
                    {allModels.find(
                      model => model.connection_id === item.model_connection_id
                    )?.model_name ?? '已绑定模型'}
                    <span className="separator">·</span>v{item.model_version}
                  </div>
                  <footer>
                    <span>{dateText(item.created_at).split(' ')[0]}</span>
                    <button
                      className="link"
                      onClick={() => setChat({ deployment: item })}
                    >
                      聊天调试 <Icon name="arrow" size={15} />
                    </button>
                  </footer>
                </article>
              ))}
          </div>
          {!available.length && !deployments.error && (
            <Empty
              text={
                deployments.loading ? '正在读取 Agent…' : '创建你的第一个 Agent'
              }
              detail="选择模型、绑定 Skill，发布一个可调试的版本。"
            />
          )}
          <div className="panel recent">
            <div className="panel-heading">
              <h2>
                最近任务 <span>当前环境</span>
              </h2>
              <button className="link" onClick={() => setPage('tasks')}>
                查看全部 <Icon name="arrow" size={14} />
              </button>
            </div>
            {taskTable(rows.slice(0, 5))}
          </div>
        </>
      )}
      {page === 'skills' && (
        <>
          <ErrorBox message={skills.error} retry={skills.refresh} />
          <div className="section-toolbar">
            <span className="muted">
              {allSkills.length} 个版本 · SKILL.md + 脚本与资源
            </span>
            <button
              className="button primary"
              disabled={!canEdit || environment !== 'test'}
              onClick={() => setDialog('upload')}
            >
              <Icon name="plus" size={16} />
              上传 Skill
            </button>
          </div>
          <div className="skill-grid">
            {allSkills.map(item => (
              <article className="skill-card" key={item.release_id}>
                <div className="skill-card-heading">
                  <span className="agent-icon green">
                    <Icon name="box" />
                  </span>
                  <span className="version">{item.version_label}</span>
                </div>
                <h2>{item.skill_name}</h2>
                <p>{item.description}</p>
                <div className="skill-card-meta">
                  <span>{item.file_count} 个文件</span>
                  <span>{item.verified ? '已验证' : '待验证'}</span>
                  <Status state={item.status} />
                </div>
                <div className="row-actions">
                  <button className="button" onClick={() => setViewSkill(item)}>
                    查看文件 <Icon name="chevron" size={14} />
                  </button>
                  <button
                    className="link danger"
                    disabled={!canEdit || item.status !== 'ready'}
                    onClick={() => setDeleteSkill(item)}
                  >
                    删除
                  </button>
                </div>
              </article>
            ))}
          </div>
          {!allSkills.length && !skills.error && (
            <Empty
              text={skills.loading ? '正在读取 Skill…' : '还没有 Skill 版本'}
              detail="上传包含 SKILL.md 的 ZIP 包，为 Agent 增加脚本与资源能力。"
            />
          )}
        </>
      )}
      {page === 'tasks' && (
        <div className="panel">
          <div className="panel-toolbar">
            <div className="category-tabs">
              {[
                ['all', '全部任务'],
                ['active', '进行中'],
                ['succeeded', '已完成'],
                ['failed', '失败与超时'],
              ].map(([value, label]) => (
                <button
                  key={value}
                  className={filter === value ? 'selected' : ''}
                  onClick={() => setFilter(value!)}
                >
                  {label}
                </button>
              ))}
            </div>
            <button className="link" onClick={runs.refresh}>
              刷新
            </button>
          </div>
          {taskTable(
            rows.filter(
              item =>
                filter === 'all' ||
                (filter === 'active'
                  ? !terminal(item.state)
                  : filter === 'failed'
                    ? ['failed', 'timed_out'].includes(item.state)
                    : item.state === filter)
            )
          )}
          <div className="inline-note">
            每个可见部署最近 50 条任务，执行中每 10
            秒更新。历史记录以服务端保存为准。
          </div>
        </div>
      )}
      {page === 'deployments' && (
        <>
          <div className="section-toolbar">
            <span className="muted">每个固定版本拥有独立 HTTP 调用地址</span>
            {environment === 'prod' && (
              <button
                className="button primary"
                disabled={!canPublish}
                onClick={() => setDialog('promote')}
              >
                从测试环境发布
              </button>
            )}
          </div>
          <div className="panel table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Agent / 版本</th>
                  <th>状态</th>
                  <th>快照摘要</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {available.map(item => (
                  <tr key={item.deployment_id}>
                    <td>
                      {item.display_name}
                      <span className="subline">{item.agent_version}</span>
                    </td>
                    <td>
                      <Status state={item.state} />
                    </td>
                    <td className="mono small">
                      {item.snapshot_sha256.slice(0, 16)}…
                    </td>
                    <td>
                      <button
                        className="link"
                        onClick={() => setCredential(item)}
                      >
                        HTTP 接口
                      </button>
                      <button
                        className="link"
                        onClick={() => setChat({ deployment: item })}
                      >
                        调试
                      </button>
                      <button
                        className="link danger"
                        disabled={
                          !(environment === 'test' ? canEdit : canPublish) ||
                          item.state === 'disabled'
                        }
                        onClick={() => setDisable(item)}
                      >
                        停用
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!available.length && <Empty />}
          </div>
        </>
      )}
      {page === 'models' && (
        <>
          <ErrorBox message={models.error} retry={models.refresh} />
          <ModelsPage
            api={api}
            models={allModels}
            canEdit={canCredential}
            canProbe={
              activeTenant &&
              environment === 'test' &&
              !!role &&
              role !== 'viewer'
            }
            refresh={models.refresh}
          />
        </>
      )}
      {page === 'tenants' && (
        <TenantPage
          api={api}
          tenant={tenant}
          platformAdmin={platformAdmin}
          admin={admin}
          changed={() => {
            changed();
            refresh();
          }}
        />
      )}
      {page === 'monitor' && (
        <>
          <div className="info-strip">
            统计范围：每个可见部署最近 50 个任务，共 {rows.length} 条。未报告的
            Token 不计为零；这里不代表全量历史或主机资源监控。
          </div>
          <div className="two-columns">
            <div className="panel resource-panel">
              <div className="panel-heading">
                <h2>任务状态分布</h2>
                <button className="link" onClick={runs.refresh}>
                  刷新
                </button>
              </div>
              <div className="status-chart">
                {[
                  'queued',
                  'running',
                  'succeeded',
                  'failed',
                  'cancelled',
                  'timed_out',
                ].map(state => {
                  const count = rows.filter(
                    item => item.state === state
                  ).length;
                  return (
                    <div key={state}>
                      <Status state={state} />
                      <div className="progress-track">
                        <span
                          style={{
                            width: `${rows.length ? (count / rows.length) * 100 : 0}%`,
                          }}
                        />
                      </div>
                      <b>{count}</b>
                    </div>
                  );
                })}
              </div>
            </div>
            <div className="panel prose-card">
              <span className="step-label">OBSERVABILITY</span>
              <h2>每次执行都有记录</h2>
              <p>
                任务中心提供状态、事件序号、工具调用、模型输出与追踪
                ID。异常执行保留错误状态，取消任务后由执行服务确认资源回收。
              </p>
              <p>CPU、内存与集群指标尚未接入此页面。</p>
              <button className="button" onClick={() => setPage('tasks')}>
                查看任务记录 <Icon name="arrow" size={15} />
              </button>
            </div>
          </div>
          <div className="panel recent">
            <div className="panel-heading">
              <h2>最近执行</h2>
            </div>
            {taskTable(rows.slice(0, 8))}
          </div>
        </>
      )}
      <footer className="page-footer">
        <span>
          <i />
          Open Agent · {environment === 'test' ? '测试环境' : '生产环境'}
        </span>
        <span>角色：{role ?? '未授权'} · 所有任务数据来自实际服务</span>
      </footer>
      {dialog === 'agent' && (
        <AgentEditor
          api={api}
          models={allModels}
          skills={allSkills}
          close={() => setDialog(undefined)}
          saved={refresh}
        />
      )}
      {dialog === 'upload' && (
        <SkillUploader
          api={api}
          close={() => setDialog(undefined)}
          saved={refresh}
        />
      )}
      {dialog === 'promote' && (
        <Promotion
          api={api}
          close={() => setDialog(undefined)}
          saved={refresh}
        />
      )}
      {chat && (
        <RunConsole
          key={`${chat.deployment.deployment_id}:${chat.run ?? ''}`}
          api={api}
          deployment={chat.deployment}
          initialRun={chat.run}
          close={() => setChat(undefined)}
          changed={runs.refresh}
        />
      )}
      {credential && (
        <Credentials
          api={api}
          deployment={credential}
          close={() => setCredential(undefined)}
          canManage={canCredential}
        />
      )}
      {viewSkill && (
        <SkillViewer
          api={api}
          skill={viewSkill}
          close={() => setViewSkill(undefined)}
        />
      )}
      {disable && (
        <EditForm
          title="停用部署"
          close={() => setDisable(undefined)}
          saved={refresh}
          submit="停用部署"
          action={() =>
            api.mutate(`agent-deployments/${disable.deployment_id}/disable`)
          }
        >
          <p>
            停用「{disable.display_name} · {disable.agent_version}
            」后将拒绝新的运行请求。历史任务和记录仍保留。
          </p>
        </EditForm>
      )}
      {deleteSkill && (
        <EditForm
          title="删除 Skill 版本"
          close={() => setDeleteSkill(undefined)}
          saved={refresh}
          submit="删除版本"
          action={() =>
            api.mutate(
              `skill-releases/${deleteSkill.release_id}`,
              undefined,
              'DELETE'
            )
          }
        >
          <p>
            删除「{deleteSkill.skill_name} · {deleteSkill.version_label}
            」。服务端会检查引用与保留期限。
          </p>
        </EditForm>
      )}
    </main>
  );
}

async function start(): Promise<void> {
  const root = createRoot(document.getElementById('root')!);
  try {
    await initializeAuth();
    const user = await auth.getUser();
    root.render(user && !user.expired ? <App user={user} /> : <Login />);
  } catch {
    root.render(<Login error="登录回调未完成或已过期，请重新登录。" />);
  }
}
void start();
