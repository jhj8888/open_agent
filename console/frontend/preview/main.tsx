import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import {
  agents as initialAgents,
  initialTasks,
  skills,
  stateNames,
  type Agent,
  type Environment,
  type State,
  type Task,
} from './data';
import './style.css';

const paths: Record<string, React.ReactNode> = {
  grid: (
    <>
      <rect x="3" y="3" width="7" height="7" rx="2" />
      <rect x="14" y="3" width="7" height="7" rx="2" />
      <rect x="3" y="14" width="7" height="7" rx="2" />
      <rect x="14" y="14" width="7" height="7" rx="2" />
    </>
  ),
  chart: (
    <>
      <path d="M4 4v16h17M8 15v-4m5 4V7m5 8v-6" />
      <path d="m7 7 5-3 6 2" />
    </>
  ),
  document: (
    <>
      <path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9zM14 3v6h6M8 13h8M8 17h5" />
    </>
  ),
  code: (
    <>
      <path d="m8 7-5 5 5 5m8-10 5 5-5 5m-3-13-2 16" />
    </>
  ),
  spark: (
    <>
      <path d="m12 3 2.7 6.3L21 12l-6.3 2.7L12 21l-2.7-6.3L3 12l6.3-2.7z" />
    </>
  ),
  terminal: (
    <>
      <rect x="3" y="4" width="18" height="16" rx="3" />
      <path d="m7 9 3 3-3 3m6 0h4" />
    </>
  ),
  shield: (
    <>
      <path d="m12 3 8 3v6c0 4-4 7-8 9-4-2-8-5-8-9V6zM8 12l3 3 5-6" />
    </>
  ),
  box: (
    <>
      <path d="m12 3 9 5-9 5-9-5zM3 8v9l9 5 9-5V8M12 13v9M8 5l9 5" />
    </>
  ),
  tasks: (
    <>
      <path d="m3 6 2 2 3-4m3 2h10M3 13l2 2 3-4m3 2h10M4 20h3m4 0h10" />
    </>
  ),
  people: (
    <>
      <circle cx="9" cy="7" r="3" />
      <path d="M3 21v-3a6 6 0 0 1 12 0v3M16 4a3 3 0 0 1 0 6m2 5a5 5 0 0 1 3 4v2" />
    </>
  ),
  pulse: <path d="M2 12h4l3-8 5 16 3-8h5" />,
  api: (
    <>
      <path d="m8 5-6 7 6 7m8-14 6 7-6 7M14 3l-4 18" />
    </>
  ),
  plus: <path d="M12 5v14M5 12h14" />,
  search: (
    <>
      <circle cx="10.5" cy="10.5" r="6.5" />
      <path d="m16 16 5 5" />
    </>
  ),
  chevron: <path d="m9 5 7 7-7 7" />,
  down: <path d="m6 9 6 6 6-6" />,
  arrow: <path d="M4 12h16m-6-6 6 6-6 6" />,
  send: (
    <>
      <path d="m3 3 19 9-19 9 3-9zM6 12h16" />
    </>
  ),
  close: <path d="m6 6 12 12M6 18 18 6" />,
  clock: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </>
  ),
  check: <path d="m5 12 4 4L19 6" />,
  copy: (
    <>
      <rect x="8" y="8" width="12" height="13" rx="2" />
      <path d="M16 8V3H3v13h5" />
    </>
  ),
  settings: (
    <>
      <path d="M4 7h16M4 17h16" />
      <circle cx="9" cy="7" r="3" fill="currentColor" />
      <circle cx="16" cy="17" r="3" fill="currentColor" />
    </>
  ),
  more: (
    <>
      <circle cx="5" cy="12" r="1" />
      <circle cx="12" cy="12" r="1" />
      <circle cx="19" cy="12" r="1" />
    </>
  ),
  refresh: (
    <>
      <path d="M20 10a8 8 0 1 0-2 8M20 4v6h-6" />
    </>
  ),
  play: <path d="m8 4 13 8-13 8z" />,
};
function Icon({
  name,
  size = 20,
}: {
  name: string;
  size?: number;
}): JSX.Element {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {paths[name] ?? paths.spark}
    </svg>
  );
}
type Page =
  | 'agents'
  | 'skills'
  | 'tasks'
  | 'deployments'
  | 'tenants'
  | 'monitor';
const pages: { id: Page; label: string; icon: string }[] = [
  { id: 'agents', label: 'Agent 工作台', icon: 'grid' },
  { id: 'skills', label: 'Skill 资源库', icon: 'box' },
  { id: 'tasks', label: '任务中心', icon: 'tasks' },
  { id: 'deployments', label: '发布与 API', icon: 'api' },
  { id: 'monitor', label: '监控概览', icon: 'pulse' },
  { id: 'tenants', label: '租户管理', icon: 'people' },
];
function Status({ state }: { state: State }): JSX.Element {
  return (
    <span className={`status ${state}`}>
      <i />
      {stateNames[state]}
    </span>
  );
}
function App(): JSX.Element {
  const [page, setPage] = useState<Page>('agents');
  const [tenant, setTenant] = useState('研发中心');
  const [environment, setEnvironment] = useState<Environment>('test');
  const [agents, setAgents] = useState<Agent[]>(initialAgents);
  const [tasks, setTasks] = useState<Task[]>(initialTasks);
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState('全部 Agent');
  const [filter, setFilter] = useState('all');
  const [chat, setChat] = useState<Agent>();
  const [taskId, setTaskId] = useState<string>();
  const [input, setInput] = useState('');
  const [create, setCreate] = useState(false);
  const [detail, setDetail] = useState<Agent>();
  const [skill, setSkill] = useState<(typeof skills)[number]>();
  const [about, setAbout] = useState(false);
  const [toast, setToast] = useState('');
  const [draft, setDraft] = useState({
    name: '',
    description: '',
    prompt: '',
    skills: ['document-reader'],
  });
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);
  const taskRef = useRef(tasks);
  taskRef.current = tasks;
  useEffect(() => () => timers.current.forEach(clearTimeout), []);
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(''), 3500);
    return () => clearTimeout(timer);
  }, [toast]);
  useEffect(() => {
    if (!chat && !create && !detail && !skill && !about) return;
    const close = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') {
        setChat(undefined);
        setCreate(false);
        setDetail(undefined);
        setSkill(undefined);
        setAbout(false);
      }
    };
    document.addEventListener('keydown', close);
    return () => document.removeEventListener('keydown', close);
  }, [chat, create, detail, skill, about]);
  const tenantAgents = useMemo(
    () =>
      agents.filter(
        a =>
          tenant === '研发中心' ||
          ['document-assistant', 'report-writer', 'contract-checker'].includes(
            a.id
          )
      ),
    [agents, tenant]
  );
  const scopedAgents = tenantAgents.filter(
    a => environment === 'test' || a.published
  );
  const shownAgents = scopedAgents.filter(
    a =>
      (category === '全部 Agent' || a.category === category) &&
      `${a.name} ${a.description} ${a.skills.join(' ')}`
        .toLowerCase()
        .includes(search.toLowerCase())
  );
  const scopedTasks = tasks.filter(
    t => t.tenant === tenant && t.environment === environment
  );
  const shownTasks = scopedTasks.filter(
    t =>
      (filter === 'all' || t.state === filter) &&
      `${t.id} ${t.agent} ${t.message}`.includes(search)
  );
  const activeTask = tasks.find(t => t.id === taskId);
  const running =
    activeTask && ['queued', 'running'].includes(activeTask.state);
  const navigate = (next: Page): void => {
    setPage(next);
    setSearch('');
    setFilter('all');
  };
  const openChat = (agent: Agent, task?: Task): void => {
    setChat(agent);
    setTaskId(task?.id);
    setInput('');
    setDetail(undefined);
  };
  const copy = async (text: string): Promise<void> => {
    try {
      if (navigator.clipboard) await navigator.clipboard.writeText(text);
      else {
        const area = document.createElement('textarea');
        area.value = text;
        document.body.appendChild(area);
        area.select();
        const ok = document.execCommand('copy');
        area.remove();
        if (!ok) throw new Error();
      }
      setToast('已复制到剪贴板');
    } catch {
      setToast('浏览器未允许复制，请选择文本手动复制。');
    }
  };
  const run = (message = input): void => {
    if (!chat || !message.trim()) return;
    const id = `DEMO-${Date.now().toString(36).toUpperCase()}`;
    const task: Task = {
      id,
      agentId: chat.id,
      agent: chat.name,
      tenant,
      environment,
      state: 'queued',
      message: message.trim(),
      answer: '',
      created: new Date().toLocaleString('sv-SE'),
      seconds: 0,
      tokens: 0,
    };
    setTasks(previous => [task, ...previous]);
    setTaskId(id);
    setInput('');
    timers.current.push(
      setTimeout(
        () =>
          setTasks(previous =>
            previous.map(t =>
              t.id === id && t.state === 'queued'
                ? { ...t, state: 'running' }
                : t
            )
          ),
        550
      )
    );
    timers.current.push(
      setTimeout(() => {
        if (taskRef.current.find(t => t.id === id)?.state === 'cancelled')
          return;
        const answer = `【界面演示 · 非模型生成】\n\n已收到你的任务：${message.trim()}\n\n这里展示「${chat.name}」的答复区域。正式接通后，将使用所选模型和 ${chat.skills.length} 个 Skill 完成任务。\n\n你现在可以体验：\n• 查看任务状态、执行步骤和日志\n• 同时创建多个演示任务\n• 取消运行、切换环境、复制 HTTP 示例\n\n本次没有调用真实模型，也没有执行上传的代码。`;
        setTasks(previous =>
          previous.map(t =>
            t.id === id && t.state !== 'cancelled'
              ? { ...t, state: 'succeeded', answer, seconds: 3.6, tokens: 0 }
              : t
          )
        );
      }, 3600)
    );
  };
  const cancel = (id: string): void => {
    setTasks(previous =>
      previous.map(t =>
        t.id === id && ['queued', 'running'].includes(t.state)
          ? {
              ...t,
              state: 'cancelled',
              answer: '演示任务已取消。',
              seconds: 1.2,
            }
          : t
      )
    );
  };
  const createAgent = (event: React.FormEvent): void => {
    event.preventDefault();
    if (!draft.name.trim() || !draft.prompt.trim()) return;
    const agent: Agent = {
      id: `demo-agent-${Date.now()}`,
      name: draft.name.trim(),
      description:
        draft.description || '新建的演示 Agent，配置仅保留在当前页面。',
      prompt: draft.prompt,
      skills: draft.skills,
      icon: 'spark',
      tone: 'green',
      version: 'v0.1.0',
      model: 'DeepSeek',
      category: '办公协作',
      updated: '刚刚',
      published: false,
    };
    setAgents(previous => [agent, ...previous]);
    setCreate(false);
    setTenant('研发中心');
    setEnvironment('test');
    setCategory('全部 Agent');
    navigate('agents');
    setToast('演示 Agent 已创建，仅保存在当前页面。');
  };
  const reset = (): void => {
    timers.current.forEach(clearTimeout);
    timers.current = [];
    setAgents(initialAgents);
    setTasks(initialTasks);
    setChat(undefined);
    setToast('已恢复初始示例数据');
  };
  const code = (agent: Agent): string =>
    `# 接口格式示例，预览站点不提供执行 API\ncurl -X POST 'https://agent.internal/api/v1/agent-deployments/${agent.id}/runs' \\\n  -H 'Authorization: Bearer <DEPLOYMENT_TOKEN>' \\\n  -H 'Idempotency-Key: <UNIQUE_REQUEST_ID>' \\\n  -H 'Content-Type: application/json' \\\n  -d '{"input":{"message":"请开始处理任务"},"timeoutSeconds":300}'`;
  const taskTable = (rows: Task[]): JSX.Element => (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>任务 / Agent</th>
            <th>状态</th>
            <th>耗时</th>
            <th>创建时间</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map(t => (
            <tr key={t.id}>
              <td>
                <button
                  className="task-link"
                  onClick={() => {
                    const a = agents.find(a => a.id === t.agentId);
                    if (a) openChat(a, t);
                  }}
                >
                  {t.agent}
                </button>
                <span className="subline mono">{t.id}</span>
              </td>
              <td>
                <Status state={t.state} />
              </td>
              <td>{t.seconds ? `${t.seconds} s` : '—'}</td>
              <td className="muted">{t.created}</td>
              <td>
                <button
                  className="link"
                  onClick={() => {
                    const a = agents.find(a => a.id === t.agentId);
                    if (a) openChat(a, t);
                  }}
                >
                  查看详情 <Icon name="chevron" size={14} />
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length === 0 && (
        <div className="empty">
          <Icon name="tasks" size={32} />
          <h3>这个范围内还没有任务</h3>
          <p>从 Agent 工作台发起一次演示任务。</p>
        </div>
      )}
    </div>
  );
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a
          className="brand"
          href="#"
          onClick={e => {
            e.preventDefault();
            navigate('agents');
          }}
        >
          <span className="brand-mark">
            <i />
            <i />
            <i />
            <i />
          </span>
          <span>
            Open Agent<small>企业智能体平台</small>
          </span>
        </a>
        <div className="workspace-label">
          WORKSPACE <span>01</span>
        </div>
        <nav aria-label="主导航">
          {pages.map(item => (
            <button
              key={item.id}
              className={page === item.id ? 'nav-item active' : 'nav-item'}
              onClick={() => navigate(item.id)}
            >
              <Icon name={item.icon} />
              <span>{item.label}</span>
              {item.id === 'agents' && <b>{scopedAgents.length}</b>}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="private-card">
            <Icon name="shield" />
            <div>
              企业私有化<small>隔离执行 · 统一管理</small>
            </div>
            <span className="tiny-dot" />
          </div>
          <button className="nav-item" onClick={() => setAbout(true)}>
            <Icon name="document" />
            <span>关于本次预览</span>
            <Icon name="chevron" size={15} />
          </button>
          <button className="profile" onClick={() => setAbout(true)}>
            <span className="avatar">OA</span>
            <span>
              平台管理员<small>预览角色</small>
            </span>
            <Icon name="more" />
          </button>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div className="breadcrumbs">
            <Icon name="grid" size={17} />
            <select
              aria-label="当前租户"
              value={tenant}
              onChange={e => {
                setTenant(e.target.value);
                setChat(undefined);
                setSearch('');
              }}
            >
              <option>研发中心</option>
              <option>运营中心</option>
            </select>
            <span>/</span>
            <span>{pages.find(p => p.id === page)?.label}</span>
          </div>
          <div className="top-actions">
            <span className="preview-version">
              PREVIEW <b>0.1</b>
            </span>
            <button
              className="icon-button"
              title="恢复示例数据"
              aria-label="恢复示例数据"
              onClick={reset}
            >
              <Icon name="refresh" size={17} />
            </button>
            <span className="small-avatar">J</span>
          </div>
        </header>
        <div className="preview-banner">
          <span className="preview-label">阶段预览</span>
          <span>
            当前为可交互界面预览，所有任务、指标与回复均为示例，未调用真实模型。
          </span>
          <button onClick={() => setAbout(true)}>
            查看说明 <Icon name="arrow" size={14} />
          </button>
        </div>
        <main>
          <div className="page-heading">
            <div>
              <div className="eyebrow">{tenant} · OPEN AGENT</div>
              <h1>{pages.find(p => p.id === page)?.label}</h1>
              <p>
                {
                  {
                    agents:
                      '从 Skill 到 Agent，在一个工作台完成创建、调试与发布。',
                    skills:
                      '管理可版本化的技能包，为 Agent 组合可靠的执行能力。',
                    tasks: '跟踪每一次执行，查看任务状态、日志与结果。',
                    deployments:
                      '管理环境与发布版本，为每个 Agent 提供统一的调用入口。',
                    tenants: '让团队拥有独立的资源、成员权限与运行配额。',
                    monitor:
                      '了解运行概况、资源使用与任务表现。以下为示例指标。',
                  }[page]
                }
              </p>
            </div>
            <div className="heading-actions">
              <div className="environment-switch" aria-label="切换环境">
                <button
                  className={environment === 'test' ? 'selected' : ''}
                  onClick={() => {
                    setEnvironment('test');
                    setChat(undefined);
                  }}
                >
                  测试环境
                </button>
                <button
                  className={
                    environment === 'prod' ? 'selected production' : ''
                  }
                  onClick={() => {
                    setEnvironment('prod');
                    setChat(undefined);
                  }}
                >
                  生产环境
                </button>
              </div>
              {page === 'agents' && (
                <button
                  className="button primary"
                  onClick={() => {
                    setDraft({
                      name: '',
                      description: '',
                      prompt: '',
                      skills: ['document-reader'],
                    });
                    setCreate(true);
                  }}
                >
                  <Icon name="plus" size={17} />
                  创建 Agent
                </button>
              )}
            </div>
          </div>
          {(page === 'agents' || page === 'monitor') && (
            <div className="stats-grid">
              <div className="stat-card">
                <div>
                  <span>Agent 总数</span>
                  <Icon name="grid" size={17} />
                </div>
                <strong>
                  {scopedAgents.length}
                  <small>个</small>
                </strong>
                <footer>
                  <span className="green-text">
                    {scopedAgents.filter(a => a.published).length} 个已发布
                  </span>
                  <span>当前环境示例</span>
                </footer>
              </div>
              <div className="stat-card">
                <div>
                  <span>运行中任务</span>
                  <Icon name="pulse" size={17} />
                </div>
                <strong>
                  {
                    scopedTasks.filter(t =>
                      ['running', 'queued'].includes(t.state)
                    ).length
                  }
                  <small>/ 8</small>
                </strong>
                <footer>
                  <span>最多并发 8 个任务</span>
                  <span className="mini-bars">
                    <i />
                    <i />
                    <i />
                    <i />
                    <i />
                  </span>
                </footer>
              </div>
              <div className="stat-card">
                <div>
                  <span>已完成任务</span>
                  <Icon name="check" size={17} />
                </div>
                <strong>
                  {scopedTasks.filter(t => t.state === 'succeeded').length}
                  <small>次</small>
                </strong>
                <footer>
                  <span className="green-text">状态可追踪</span>
                  <span>演示数据</span>
                </footer>
              </div>
              <div className="stat-card">
                <div>
                  <span>Skill 资源</span>
                  <Icon name="box" size={17} />
                </div>
                <strong>
                  {skills.length}
                  <small>个</small>
                </strong>
                <footer>
                  <span>固定版本 · 资源包</span>
                  <span className="green-text">SKILL.md</span>
                </footer>
              </div>
            </div>
          )}
          {page === 'agents' && (
            <>
              <div className="section-toolbar">
                <div className="category-tabs">
                  {[
                    '全部 Agent',
                    '数据分析',
                    '办公协作',
                    '研发工具',
                    '业务支持',
                  ].map(c => (
                    <button
                      key={c}
                      className={category === c ? 'selected' : ''}
                      onClick={() => setCategory(c)}
                    >
                      {c}
                      {c === '全部 Agent' && <span>{scopedAgents.length}</span>}
                    </button>
                  ))}
                </div>
                <label className="search">
                  <Icon name="search" size={17} />
                  <input
                    placeholder="搜索 Agent、技能…"
                    value={search}
                    onChange={e => setSearch(e.target.value)}
                  />
                </label>
              </div>
              <div className="agent-grid">
                {shownAgents.map(agent => (
                  <article className="agent-card" key={agent.id}>
                    <div className="agent-card-top">
                      <span className={`agent-icon ${agent.tone}`}>
                        <Icon name={agent.icon} size={25} />
                      </span>
                      <span
                        className={`ready-badge ${agent.published ? '' : 'draft'}`}
                      >
                        <i />
                        {agent.published ? '已发布' : '测试中'}
                      </span>
                      <button
                        className="icon-button"
                        aria-label={`${agent.name}详情`}
                        onClick={() => setDetail(agent)}
                      >
                        <Icon name="more" size={18} />
                      </button>
                    </div>
                    <h2>
                      {agent.name}
                      <span>{agent.version}</span>
                    </h2>
                    <p>{agent.description}</p>
                    <div className="skill-tags">
                      {agent.skills.map(name => (
                        <span key={name}>
                          <Icon name="box" size={12} />
                          {name}
                        </span>
                      ))}
                    </div>
                    <div className="agent-model">
                      <span className="model-dot">D</span>
                      {agent.model}
                      <span className="separator">·</span>
                      <span>{agent.skills.length} 个 Skills</span>
                      <span className="update-time">{agent.updated}</span>
                    </div>
                    <footer>
                      <span>
                        <Icon name="shield" size={14} />
                        独立任务沙盒
                      </span>
                      <button onClick={() => openChat(agent)}>
                        开始调试 <Icon name="arrow" size={15} />
                      </button>
                    </footer>
                  </article>
                ))}
              </div>
              {shownAgents.length === 0 && (
                <div className="empty">
                  <Icon name="search" size={30} />
                  <h3>没有找到匹配的 Agent</h3>
                  <p>换个关键词，或创建新的 Agent。</p>
                </div>
              )}
              <section className="panel recent">
                <div className="panel-heading">
                  <h2>
                    最近任务{' '}
                    <span>
                      当前租户 / {environment === 'test' ? '测试' : '生产'}环境
                    </span>
                  </h2>
                  <button className="link" onClick={() => navigate('tasks')}>
                    查看全部 <Icon name="arrow" size={15} />
                  </button>
                </div>
                {taskTable(scopedTasks.slice(0, 3))}
              </section>
            </>
          )}
          {page === 'tasks' && (
            <section className="panel">
              <div className="section-toolbar panel-toolbar">
                <div className="category-tabs">
                  {[
                    ['all', '全部任务'],
                    ['running', '运行中'],
                    ['succeeded', '已完成'],
                    ['failed', '失败'],
                    ['cancelled', '已取消'],
                  ].map(([value, label]) => (
                    <button
                      key={value}
                      className={filter === value ? 'selected' : ''}
                      onClick={() => setFilter(value ?? 'all')}
                    >
                      {label}
                    </button>
                  ))}
                </div>
                <label className="search">
                  <Icon name="search" size={17} />
                  <input
                    placeholder="搜索任务 ID 或 Agent…"
                    value={search}
                    onChange={e => setSearch(e.target.value)}
                  />
                </label>
              </div>
              {taskTable(shownTasks)}
            </section>
          )}
          {page === 'skills' && (
            <>
              <div className="section-toolbar">
                <div>
                  <b>{skills.length} 个技能包</b>
                  <span className="inline-note">
                    SKILL.md + 脚本 / 资源文件
                  </span>
                </div>
                <button
                  className="button primary"
                  onClick={() => document.getElementById('skill-file')?.click()}
                >
                  <Icon name="plus" size={17} />
                  选择技能包
                </button>
                <input
                  id="skill-file"
                  type="file"
                  accept=".zip"
                  hidden
                  onChange={e => {
                    const file = e.target.files?.[0];
                    if (file)
                      setToast(
                        `已选择 ${file.name}。预览模式不上传或执行文件。`
                      );
                  }}
                />
              </div>
              <div className="skill-grid">
                {skills.map(s => (
                  <article className="panel skill-card" key={s.name}>
                    <div className="skill-card-heading">
                      <span className="agent-icon green">
                        <Icon name="box" size={23} />
                      </span>
                      <span className="version">v{s.version}</span>
                    </div>
                    <h2>{s.title}</h2>
                    <div className="mono small muted">{s.name}</div>
                    <p>{s.description}</p>
                    <div className="skill-card-meta">
                      <span>{s.files} 个文件</span>
                      <span>{s.size}</span>
                      <span>{s.runtime}</span>
                    </div>
                    <button className="button full" onClick={() => setSkill(s)}>
                      查看技能包 <Icon name="arrow" size={15} />
                    </button>
                  </article>
                ))}
              </div>
            </>
          )}
          {page === 'deployments' && (
            <>
              <div className="info-strip">
                <Icon name="shield" />
                <span>
                  生产环境使用固定发布版本，调用凭据限定到单个部署。以下为接口格式预览。
                </span>
              </div>
              <section className="panel">
                <div className="panel-heading">
                  <h2>已发布 Agent</h2>
                  <span className="muted small">
                    示例端点 · 尚未接通执行 API
                  </span>
                </div>
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>Agent</th>
                        <th>发布版本</th>
                        <th>环境</th>
                        <th>调用方式</th>
                        <th />
                      </tr>
                    </thead>
                    <tbody>
                      {scopedAgents
                        .filter(a => a.published)
                        .map(a => (
                          <tr key={a.id}>
                            <td>
                              <b>{a.name}</b>
                              <span className="subline mono">{a.id}</span>
                            </td>
                            <td>
                              <span className="version">{a.version}</span>
                            </td>
                            <td>
                              <span className="environment-tag">
                                {environment === 'test' ? '测试' : '生产'}
                              </span>
                            </td>
                            <td>
                              <span className="method">POST</span>
                              <span className="mono small">
                                {' '}
                                /agent-deployments/…/runs
                              </span>
                            </td>
                            <td>
                              <button
                                className="link"
                                onClick={() => setDetail(a)}
                              >
                                调用示例 <Icon name="api" size={15} />
                              </button>
                            </td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                </div>
              </section>
              <div className="two-columns">
                <section className="panel prose-card">
                  <span className="step-label">01 / 发布</span>
                  <h2>版本固定，可追溯</h2>
                  <p>
                    Agent 配置、模型连接与 Skill
                    版本组成部署快照。测试通过后由有权限的人员发布到生产环境。
                  </p>
                </section>
                <section className="panel prose-card">
                  <span className="step-label">02 / 调用</span>
                  <h2>一次提交，持续跟踪</h2>
                  <p>
                    HTTP 与聊天窗口使用相同任务
                    ID。断开页面不取消任务，通过事件流继续查看执行进展。
                  </p>
                </section>
              </div>
            </>
          )}
          {page === 'tenants' && (
            <>
              <section className="panel">
                <div className="panel-heading">
                  <h2>租户空间</h2>
                  <span className="muted small">示例成员与配额</span>
                </div>
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>租户</th>
                        <th>环境</th>
                        <th>成员</th>
                        <th>任务并发 / 沙盒</th>
                        <th>状态</th>
                        <th />
                      </tr>
                    </thead>
                    <tbody>
                      {['研发中心', '运营中心'].map((name, i) => (
                        <tr key={name}>
                          <td>
                            <b>{name}</b>
                            <span className="subline mono">
                              {i ? 'team-operations' : 'team-research'}
                            </span>
                          </td>
                          <td>测试 / 生产</td>
                          <td>{i ? 6 : 12} 位</td>
                          <td>{i ? '4 / 4' : '8 / 8'}</td>
                          <td>
                            <span className="status succeeded">
                              <i />
                              已启用
                            </span>
                          </td>
                          <td>
                            <button
                              className="link"
                              onClick={() => {
                                setTenant(name);
                                navigate('agents');
                              }}
                            >
                              进入空间 <Icon name="arrow" size={15} />
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
              <section className="panel permission-panel">
                <div className="panel-heading">
                  <h2>成员角色</h2>
                  <span className="muted small">生产调用还需要部署级授权</span>
                </div>
                <div className="role-grid">
                  {[
                    [
                      'tenant_admin',
                      '租户管理员',
                      '管理成员、环境配额与调用凭据。',
                    ],
                    [
                      'developer',
                      '开发者',
                      '配置测试 Agent、组合 Skills 并调试。',
                    ],
                    ['operator', '运维', '发布生产版本、查看日志和取消任务。'],
                    ['viewer', '只读成员', '查看有权限的 Agent 元数据。'],
                  ].map(([id, name, text]) => (
                    <div key={id}>
                      <span className="role-icon">
                        <Icon name="people" />
                      </span>
                      <h3>{name}</h3>
                      <p>{text}</p>
                      <code>{id}</code>
                    </div>
                  ))}
                </div>
              </section>
            </>
          )}
          {page === 'monitor' && (
            <>
              <div className="two-columns">
                <section className="panel chart-panel">
                  <div className="panel-heading">
                    <h2>
                      任务趋势 <span>示例分布</span>
                    </h2>
                    <span className="version">最近 7 天</span>
                  </div>
                  <div className="bar-chart">
                    {[38, 57, 46, 78, 63, 92, 71].map((height, i) => (
                      <div key={i}>
                        <div className="bar-track">
                          <span style={{ height: `${height}%` }} />
                          <i
                            style={{ height: `${Math.round(height * 0.65)}%` }}
                          />
                        </div>
                        <label>
                          {
                            [
                              '周四',
                              '周五',
                              '周六',
                              '周日',
                              '周一',
                              '周二',
                              '周三',
                            ][i]
                          }
                        </label>
                      </div>
                    ))}
                  </div>
                  <div className="chart-legend">
                    <span>
                      <i />
                      全部任务
                    </span>
                    <span>
                      <i />
                      已完成
                    </span>
                  </div>
                </section>
                <section className="panel resource-panel">
                  <div className="panel-heading">
                    <h2>
                      资源配额 <span>示例</span>
                    </h2>
                    <Icon name="settings" size={18} />
                  </div>
                  {[
                    ['执行槽位', '3 / 8', 37],
                    ['沙盒实例', '2 / 8', 25],
                    ['队列容量', '5 / 32', 16],
                  ].map(([name, amount, value]) => (
                    <div className="resource-row" key={name}>
                      <div>
                        <span>{name}</span>
                        <b>{amount}</b>
                      </div>
                      <div className="progress-track">
                        <span style={{ width: `${value}%` }} />
                      </div>
                    </div>
                  ))}
                  <div className="resource-note">
                    <Icon name="shield" size={18} />
                    <span>
                      模型部署与推理由独立团队负责，平台通过模型接口调用。
                    </span>
                  </div>
                </section>
              </div>
              <section className="panel recent">
                <div className="panel-heading">
                  <h2>任务日志</h2>
                  <span className="muted small">示例日志 · 不含模型密钥</span>
                </div>
                <div className="log-view">
                  <span>
                    14:32:18 <b>INFO</b> run.accepted tenant=team-research
                    environment=test
                  </span>
                  <span>
                    14:32:19 <b>INFO</b> sandbox.reserved run=DEMO-0218
                  </span>
                  <span>
                    14:32:20 <b>INFO</b> skill.loaded
                    release=data-analysis@1.4.0
                  </span>
                  <span>
                    14:32:36 <b>INFO</b> run.succeeded run=DEMO-0218
                  </span>
                  <span>
                    14:32:37 <b>INFO</b> cleanup.reported run=DEMO-0218
                  </span>
                </div>
              </section>
            </>
          )}
          <footer className="page-footer">
            <span>
              Open Agent <i /> 企业 Agent 平台
            </span>
            <span>阶段界面预览 · v0.1 · 数据仅保留在当前页面</span>
          </footer>
        </main>
      </div>
      {toast && (
        <div className="toast" role="status">
          <Icon name="check" size={17} />
          {toast}
        </div>
      )}
      {chat && (
        <div
          className="drawer-backdrop"
          onMouseDown={e => {
            if (e.target === e.currentTarget) setChat(undefined);
          }}
        >
          <section
            className="chat-drawer"
            role="dialog"
            aria-modal="true"
            aria-label="Agent 聊天调试"
          >
            <header className="drawer-heading">
              <span className={`agent-icon ${chat.tone}`}>
                <Icon name={chat.icon} size={22} />
              </span>
              <div>
                <h2>
                  {chat.name}
                  <span>{chat.version}</span>
                </h2>
                <p>
                  {environment === 'test' ? '测试环境' : '生产环境示例'} ·{' '}
                  {chat.model} · {chat.skills.length} 个 Skills
                </p>
              </div>
              <button
                className="icon-button"
                aria-label="关闭调试"
                onClick={() => setChat(undefined)}
              >
                <Icon name="close" />
              </button>
            </header>
            <div className="chat-preview-note">
              交互演示：回复由页面生成，不调用模型、不执行代码。
            </div>
            <div className="chat-toolbar">
              <span>
                <Icon name="terminal" size={15} />
                调试会话
              </span>
              <button
                className="link"
                onClick={() => {
                  setTaskId(undefined);
                  setInput('');
                }}
              >
                <Icon name="plus" size={14} />
                新建任务
              </button>
            </div>
            <div className="chat-scroll">
              {!activeTask ? (
                <div className="chat-welcome">
                  <span className={`agent-icon large ${chat.tone}`}>
                    <Icon name={chat.icon} size={32} />
                  </span>
                  <h2>开始一次调试</h2>
                  <p>输入一个任务，体验从提交到结果返回的完整界面。</p>
                  <div className="suggestions">
                    {[
                      '帮我分析本月运营数据',
                      '整理会议纪要和行动项',
                      '解释这个 Agent 的工作流程',
                    ].map(text => (
                      <button key={text} onClick={() => setInput(text)}>
                        {text}
                        <Icon name="arrow" size={15} />
                      </button>
                    ))}
                  </div>
                </div>
              ) : (
                <>
                  <div className="chat-message user-message">
                    <span className="message-avatar">我</span>
                    <div>{activeTask.message}</div>
                  </div>
                  <div className="chat-message assistant-message">
                    <span className="message-avatar agent">
                      <Icon name="spark" size={18} />
                    </span>
                    <div>
                      <div className="message-label">
                        {chat.name}
                        <span>演示答复</span>
                      </div>
                      {running ? (
                        <div className="thinking">
                          <span />
                          <span />
                          <span />
                          {activeTask.state === 'queued'
                            ? '等待演示任务开始…'
                            : '正在展示执行过程…'}
                        </div>
                      ) : (
                        <div className="answer-text">{activeTask.answer}</div>
                      )}
                      <details
                        className="execution-details"
                        open={Boolean(running)}
                      >
                        <summary>
                          <Icon name="tasks" size={15} />
                          执行步骤（模拟）
                          <Status state={activeTask.state} />
                        </summary>
                        <ol>
                          {[
                            '任务已接入',
                            '预留独立沙盒',
                            '装载 Skill 版本',
                            '处理任务并生成结果',
                            '清理资源与更新状态',
                          ].map((text, i) => (
                            <li
                              key={text}
                              className={
                                activeTask.state === 'succeeded' ||
                                (activeTask.state === 'running' && i < 3)
                                  ? 'done'
                                  : ''
                              }
                            >
                              <span>
                                {activeTask.state === 'succeeded' ||
                                (activeTask.state === 'running' && i < 3) ? (
                                  <Icon name="check" size={12} />
                                ) : (
                                  i + 1
                                )}
                              </span>
                              {text}
                            </li>
                          ))}
                        </ol>
                      </details>
                      <div className="task-meta">
                        <span className="mono">{activeTask.id}</span>
                        <span>
                          {activeTask.seconds
                            ? `${activeTask.seconds} s`
                            : '进行中'}
                        </span>
                        <button
                          className="link"
                          onClick={() => void copy(activeTask.id)}
                        >
                          <Icon name="copy" size={12} />
                          复制 ID
                        </button>
                      </div>
                    </div>
                  </div>
                </>
              )}
            </div>
            <form
              className="chat-composer"
              onSubmit={e => {
                e.preventDefault();
                run();
              }}
            >
              <textarea
                aria-label="调试消息"
                placeholder="输入任务，体验 Agent 调试流程…"
                value={input}
                onChange={e => setInput(e.target.value)}
                maxLength={2000}
                rows={3}
                disabled={Boolean(running)}
                onKeyDown={e => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    if (!running) run();
                  }
                }}
              />
              <div>
                <span>Enter 发送 · Shift + Enter 换行</span>
                {running ? (
                  <button
                    type="button"
                    className="button"
                    onClick={() => cancel(activeTask.id)}
                  >
                    取消任务
                  </button>
                ) : (
                  <button
                    className="button primary"
                    disabled={!input.trim()}
                    type="submit"
                  >
                    发送 <Icon name="send" size={15} />
                  </button>
                )}
              </div>
            </form>
          </section>
        </div>
      )}
      {create && (
        <div className="modal-backdrop">
          <section
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-label="创建 Agent"
          >
            <header>
              <div>
                <span className="eyebrow">NEW AGENT</span>
                <h2>创建 Agent</h2>
              </div>
              <button
                className="icon-button"
                aria-label="关闭创建"
                onClick={() => setCreate(false)}
              >
                <Icon name="close" />
              </button>
            </header>
            <form onSubmit={createAgent}>
              <div className="form-body">
                <label>
                  Agent 名称 <em>*</em>
                  <input
                    autoFocus
                    value={draft.name}
                    onChange={e => setDraft({ ...draft, name: e.target.value })}
                    placeholder="例如：项目资料助手"
                    maxLength={60}
                    required
                  />
                </label>
                <label>
                  用途说明
                  <input
                    value={draft.description}
                    onChange={e =>
                      setDraft({ ...draft, description: e.target.value })
                    }
                    placeholder="用一句话描述它能做什么"
                    maxLength={180}
                  />
                </label>
                <div className="form-row">
                  <label>
                    运行引擎
                    <input value="DeepAgents" readOnly />
                  </label>
                  <label>
                    模型接口
                    <input value="DeepSeek（预览占位）" readOnly />
                  </label>
                </div>
                <label>
                  Agent 指令 <em>*</em>
                  <textarea
                    value={draft.prompt}
                    onChange={e =>
                      setDraft({ ...draft, prompt: e.target.value })
                    }
                    placeholder="说明 Agent 的职责、处理步骤和输出要求…"
                    rows={4}
                    maxLength={5000}
                    required
                  />
                </label>
                <label>绑定 Skills</label>
                <div className="skill-picker">
                  {skills.map(s => (
                    <label key={s.name}>
                      <input
                        type="checkbox"
                        checked={draft.skills.includes(s.name)}
                        onChange={e =>
                          setDraft({
                            ...draft,
                            skills: e.target.checked
                              ? [...draft.skills, s.name]
                              : draft.skills.filter(name => name !== s.name),
                          })
                        }
                      />
                      <span>
                        {s.title}
                        <small>{s.name}</small>
                      </span>
                      <span className="version">{s.version}</span>
                    </label>
                  ))}
                </div>
                <p className="form-note">
                  预览配置仅保留在当前页面，不发布到实际环境。
                </p>
              </div>
              <footer>
                <button
                  type="button"
                  className="button"
                  onClick={() => setCreate(false)}
                >
                  取消
                </button>
                <button type="submit" className="button primary">
                  创建演示 Agent <Icon name="arrow" size={15} />
                </button>
              </footer>
            </form>
          </section>
        </div>
      )}
      {detail && (
        <div className="modal-backdrop">
          <section
            className="modal wide"
            role="dialog"
            aria-modal="true"
            aria-label="Agent 详情"
          >
            <header>
              <div>
                <span className="eyebrow">AGENT DETAILS</span>
                <h2>
                  {detail.name}
                  <span className="version">{detail.version}</span>
                </h2>
              </div>
              <button
                className="icon-button"
                aria-label="关闭详情"
                onClick={() => setDetail(undefined)}
              >
                <Icon name="close" />
              </button>
            </header>
            <div className="form-body">
              <p className="muted">{detail.description}</p>
              <div className="detail-grid">
                <div>
                  <span>模型</span>
                  <b>{detail.model}</b>
                </div>
                <div>
                  <span>运行引擎</span>
                  <b>DeepAgents</b>
                </div>
                <div>
                  <span>Skills</span>
                  <b>{detail.skills.length} 个固定版本</b>
                </div>
              </div>
              <h3>Agent 指令</h3>
              <pre className="prompt-preview">{detail.prompt}</pre>
              <div className="code-heading">
                <h3>HTTP 调用示例</h3>
                <button
                  className="link"
                  onClick={() => void copy(code(detail))}
                >
                  <Icon name="copy" size={14} />
                  复制
                </button>
              </div>
              <pre className="code-block">{code(detail)}</pre>
              <p className="form-note">
                此处为目标 API
                格式。预览站点不接收执行请求，不需要输入真实密钥。
              </p>
            </div>
            <footer>
              <button
                className="button"
                onClick={() => {
                  setDraft({
                    name: `${detail.name} · 新版本`,
                    description: detail.description,
                    prompt: detail.prompt,
                    skills: detail.skills,
                  });
                  setDetail(undefined);
                  setCreate(true);
                }}
              >
                新建版本
              </button>
              <button
                className="button primary"
                onClick={() => openChat(detail)}
              >
                开始调试 <Icon name="arrow" size={15} />
              </button>
            </footer>
          </section>
        </div>
      )}
      {skill && (
        <div className="modal-backdrop">
          <section
            className="modal wide"
            role="dialog"
            aria-modal="true"
            aria-label="Skill 详情"
          >
            <header>
              <div>
                <span className="eyebrow">SKILL PACKAGE</span>
                <h2>
                  {skill.title}
                  <span className="version">v{skill.version}</span>
                </h2>
              </div>
              <button
                className="icon-button"
                aria-label="关闭技能详情"
                onClick={() => setSkill(undefined)}
              >
                <Icon name="close" />
              </button>
            </header>
            <div className="form-body">
              <p className="muted">{skill.description}</p>
              <div className="package-layout">
                <div className="file-tree">
                  <b>{skill.name}/</b>
                  <span className="selected">
                    <Icon name="document" size={15} />
                    SKILL.md
                  </span>
                  <span>
                    <Icon name="code" size={15} />
                    scripts/main.py
                  </span>
                  <span>
                    <Icon name="box" size={15} />
                    resources/
                  </span>
                  <span>
                    <Icon name="document" size={15} />
                    requirements.lock
                  </span>
                </div>
                <pre className="skill-content">{`---\nname: ${skill.name}\ndescription: ${skill.description}\n---\n\n# ${skill.title}\n\n## 使用说明\n1. 确认任务输入与所需文件。\n2. 在独立沙盒中执行脚本。\n3. 整理输出并说明结果依据。\n\n此内容为界面预览示例，不执行真实脚本。`}</pre>
              </div>
            </div>
            <footer>
              <span className="small muted">
                {skill.files} 个文件 · {skill.size} · {skill.runtime}
              </span>
              <button className="button" onClick={() => setSkill(undefined)}>
                关闭
              </button>
            </footer>
          </section>
        </div>
      )}
      {about && (
        <div className="modal-backdrop">
          <section
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-label="预览说明"
          >
            <header>
              <div>
                <span className="eyebrow">PREVIEW 0.1</span>
                <h2>先看工作台，再接通执行</h2>
              </div>
              <button
                className="icon-button"
                aria-label="关闭说明"
                onClick={() => setAbout(false)}
              >
                <Icon name="close" />
              </button>
            </header>
            <div className="form-body about-content">
              <p>
                这是 Open Agent 的阶段界面预览，用于确认页面布局和交互流程。
              </p>
              <h3>现在可以体验</h3>
              <p>
                浏览和搜索 Agent、创建演示配置、聊天调试、任务取消与查询、Skill
                包详情、环境切换、接口示例和租户概览。
              </p>
              <h3>当前边界</h3>
              <p>
                所有任务、指标、日志和回复都是示例或浏览器模拟。页面不会调用模型、上传业务文件、执行代码或生成真实密钥。刷新页面会恢复初始数据。
              </p>
              <h3>后续接入</h3>
              <p>
                实际模型与 Skill
                沙盒执行已有单独验证；接下来把持久任务、真实执行、权限和环境发布接入这套界面。完整平台仍在开发。
              </p>
              <a
                href="https://github.com/jhj8888/open_agent"
                target="_blank"
                rel="noreferrer"
              >
                查看项目仓库 ↗
              </a>
            </div>
            <footer>
              <button
                className="button primary"
                onClick={() => setAbout(false)}
              >
                开始体验 <Icon name="arrow" size={15} />
              </button>
            </footer>
          </section>
        </div>
      )}
    </div>
  );
}

const root = document.getElementById('root');
if (root)
  createRoot(root).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>
  );
