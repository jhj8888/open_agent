export type Environment = 'test' | 'prod';
export type State = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';
export interface Agent {
  id: string;
  name: string;
  description: string;
  icon: string;
  tone: string;
  version: string;
  model: string;
  skills: string[];
  category: string;
  updated: string;
  published: boolean;
  prompt: string;
}
export interface Task {
  id: string;
  agentId: string;
  agent: string;
  tenant: string;
  environment: Environment;
  state: State;
  message: string;
  answer: string;
  created: string;
  seconds: number;
  tokens: number;
}
export const agents: Agent[] = [
  {
    id: 'data-analyst',
    name: '数据分析助手',
    description: '读取运营报表，分析关键指标与变化趋势，生成清晰的分析结论。',
    icon: 'chart',
    tone: 'green',
    version: 'v1.3.0',
    model: 'DeepSeek',
    skills: ['data-analysis', 'report-writer'],
    category: '数据分析',
    updated: '10 分钟前',
    published: true,
    prompt:
      '你是企业数据分析助手。先检查数据完整性，再解释关键变化，注明结论依据。',
  },
  {
    id: 'document-assistant',
    name: '文档整理助手',
    description: '提取长文档要点，整理会议纪要、行动项与知识摘要。',
    icon: 'document',
    tone: 'blue',
    version: 'v2.1.0',
    model: 'DeepSeek',
    skills: ['document-reader', 'meeting-notes'],
    category: '办公协作',
    updated: '35 分钟前',
    published: true,
    prompt: '将文档整理成结构清晰的摘要与行动项，保留重要事实和出处。',
  },
  {
    id: 'code-reviewer',
    name: '代码审查助手',
    description: '检查代码变更、定位潜在问题，并在隔离环境中验证修复思路。',
    icon: 'code',
    tone: 'orange',
    version: 'v0.8.2',
    model: 'DeepSeek',
    skills: ['code-review', 'python-tools'],
    category: '研发工具',
    updated: '1 小时前',
    published: false,
    prompt: '审查代码的正确性、可维护性和权限边界，只报告有依据的问题。',
  },
  {
    id: 'report-writer',
    name: '经营周报助手',
    description: '汇总多份业务材料，生成包含进展、风险与下周计划的经营周报。',
    icon: 'spark',
    tone: 'violet',
    version: 'v1.0.4',
    model: 'DeepSeek',
    skills: ['report-writer', 'document-reader'],
    category: '办公协作',
    updated: '2 小时前',
    published: true,
    prompt: '根据提供的材料编写经营周报，区分已完成事项、当前风险与后续计划。',
  },
  {
    id: 'ops-assistant',
    name: '运维排障助手',
    description: '梳理服务日志和故障现象，给出可验证的定位步骤与处置建议。',
    icon: 'terminal',
    tone: 'slate',
    version: 'v0.6.0',
    model: 'DeepSeek',
    skills: ['log-analysis'],
    category: '研发工具',
    updated: '昨天',
    published: false,
    prompt: '基于日志证据分析故障，区分现象、原因假设和验证步骤。',
  },
  {
    id: 'contract-checker',
    name: '合同信息提取',
    description: '提取合同主体、关键日期与交付条款，输出便于核对的结构化信息。',
    icon: 'shield',
    tone: 'teal',
    version: 'v1.2.1',
    model: 'DeepSeek',
    skills: ['document-reader', 'contract-fields'],
    category: '业务支持',
    updated: '昨天',
    published: true,
    prompt: '提取合同信息并标出原文位置，无法确认的字段留空，不替代专业审核。',
  },
];
export const skills = [
  {
    name: 'data-analysis',
    title: '数据分析',
    version: '1.4.0',
    files: 8,
    description: 'CSV / JSON 数据检查与统计分析',
    runtime: 'Python 3.11',
    size: '32.6 KB',
  },
  {
    name: 'document-reader',
    title: '文档读取',
    version: '2.0.1',
    files: 6,
    description: '文档内容提取与结构化整理',
    runtime: 'Python 3.11',
    size: '18.2 KB',
  },
  {
    name: 'report-writer',
    title: '报告生成',
    version: '1.2.0',
    files: 5,
    description: '根据模板生成结构清晰的业务报告',
    runtime: 'Python 3.11',
    size: '24.1 KB',
  },
  {
    name: 'code-review',
    title: '代码审查',
    version: '0.8.0',
    files: 12,
    description: '代码变更检查与结果格式化',
    runtime: 'Python 3.11',
    size: '46.8 KB',
  },
  {
    name: 'meeting-notes',
    title: '会议纪要',
    version: '1.0.0',
    files: 3,
    description: '会议要点、决策和行动项提取',
    runtime: 'Python 3.11',
    size: '9.4 KB',
  },
  {
    name: 'log-analysis',
    title: '日志分析',
    version: '0.6.0',
    files: 7,
    description: '日志归类、异常聚合与时间线整理',
    runtime: 'Python 3.11',
    size: '21.7 KB',
  },
];
export const stateNames: Record<State, string> = {
  queued: '排队中',
  running: '运行中',
  succeeded: '已完成',
  failed: '失败',
  cancelled: '已取消',
};
export const initialTasks: Task[] = [
  {
    id: 'DEMO-0218',
    agentId: 'data-analyst',
    agent: '数据分析助手',
    tenant: '研发中心',
    environment: 'test',
    state: 'succeeded',
    message: '分析本月各业务线的关键指标。',
    answer:
      '【示例结果】本月业务总体保持稳定。这里展示分析结论、数据依据与后续建议的呈现方式。\n\n真实执行接通后，结果将来自你所选的模型接口与 Skill，不使用这段示例文案。',
    created: '2026-11-04 14:32:18',
    seconds: 18.4,
    tokens: 2340,
  },
  {
    id: 'DEMO-0217',
    agentId: 'document-assistant',
    agent: '文档整理助手',
    tenant: '研发中心',
    environment: 'test',
    state: 'succeeded',
    message: '整理项目评审会议纪要。',
    answer:
      '【示例结果】已整理会议要点、决策项和后续行动。本结果仅用于预览界面。',
    created: '2026-11-04 14:28:05',
    seconds: 12.7,
    tokens: 1816,
  },
  {
    id: 'DEMO-0216',
    agentId: 'code-reviewer',
    agent: '代码审查助手',
    tenant: '研发中心',
    environment: 'test',
    state: 'failed',
    message: '检查本次代码变更。',
    answer:
      '【模拟失败】展示任务失败状态、错误码和重试入口。此任务没有执行真实代码。',
    created: '2026-11-04 14:20:41',
    seconds: 4.2,
    tokens: 0,
  },
  {
    id: 'DEMO-0215',
    agentId: 'report-writer',
    agent: '经营周报助手',
    tenant: '研发中心',
    environment: 'prod',
    state: 'succeeded',
    message: '生成本周经营报告。',
    answer:
      '【生产环境示例】这里展示已发布 Agent 的运行记录，不代表已经接入生产环境。',
    created: '2026-11-04 13:50:09',
    seconds: 21.3,
    tokens: 2846,
  },
  {
    id: 'DEMO-0214',
    agentId: 'document-assistant',
    agent: '文档整理助手',
    tenant: '运营中心',
    environment: 'test',
    state: 'succeeded',
    message: '整理活动策划要点。',
    answer: '【示例结果】活动要点已归纳为目标、执行计划与待确认事项。',
    created: '2026-11-04 13:42:12',
    seconds: 15.1,
    tokens: 1902,
  },
];
