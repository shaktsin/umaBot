import type {
  AgentSkillItem,
  AgentTeam,
  AgentTeamRun,
  Attachment,
  AuditEntry,
  Connector,
  LLMProvidersResponse,
  MCPMethodsResponse,
  MCPServerTestResponse,
  MCPServersResponse,
  PendingConfirmation,
  Skill,
  StatusResponse,
  StatsResponse,
  Task,
} from './types';

async function fetchJson<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(path, options);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText })) as { detail?: string };
    throw new Error(err.detail ?? 'Request failed');
  }
  return res.json() as Promise<T>;
}

const json = (body: unknown) => ({
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

export const api = {
  // Dashboard
  getStatus: () => fetchJson<StatusResponse>('/api/status'),
  getStats: () => fetchJson<StatsResponse>('/api/stats'),
  getActivity: (limit = 30) => fetchJson<AuditEntry[]>(`/api/activity?limit=${limit}`),

  // Connectors
  getConnectors: () => fetchJson<Connector[]>('/api/connectors'),

  // Skills
  getSkills: () => fetchJson<Skill[]>('/api/skills'),
  installSkill: (source: string, name?: string) =>
    fetchJson('/api/skills/install', { method: 'POST', ...json({ source, name }) }),
  removeSkill: (name: string) => fetchJson(`/api/skills/${name}`, { method: 'DELETE' }),

  // Tasks
  getTasks: (status?: string) => fetchJson<Task[]>(`/api/tasks${status ? `?status=${status}` : ''}`),
  createTask: (data: Partial<Task> & { prompt: string; task_type: string; name: string }) =>
    fetchJson<Task>('/api/tasks', { method: 'POST', ...json(data) }),
  cancelTask: (id: number) => fetchJson(`/api/tasks/${id}`, { method: 'DELETE' }),
  getTaskRuns: (id: number) => fetchJson(`/api/tasks/${id}/runs`),

  // Policy
  getPending: () => fetchJson<PendingConfirmation[]>('/api/policy/pending'),
  confirmAction: (token: string, approved: boolean) =>
    fetchJson('/api/policy/confirm', { method: 'POST', ...json({ token, approved }) }),
  approveAgentAction: (token: string, approved: boolean) =>
    fetchJson('/api/policy/agents/approve', { method: 'POST', ...json({ token, approved }) }),
  getAudit: (limit = 100, event_type = '') =>
    fetchJson<AuditEntry[]>(`/api/policy/audit?limit=${limit}${event_type ? `&event_type=${event_type}` : ''}`),
  getPolicySettings: () =>
    fetchJson<{
      confirmation_strictness: string;
      shell_enabled: boolean;
      approval_mode: string;
      auto_approve_workspaces: string[];
      auto_approve_tools: string[];
      auto_approve_shell_commands: string[];
    }>('/api/policy/settings'),

  // Config
  getConfig: () => fetchJson<Record<string, unknown>>('/api/config'),
  updateConfig: (data: Record<string, unknown>) =>
    fetchJson('/api/config', { method: 'PUT', ...json(data) }),
  getRawYaml: () => fetchJson<{ yaml: string; path: string }>('/api/config/raw'),

  // Admin
  getLlmProviders: () => fetchJson<LLMProvidersResponse>('/api/admin/llm-providers'),
  updateLlmProvider: (
    provider: string,
    data: {
      enabled?: boolean;
      models?: string[];
      default_model?: string;
      api_key?: string;
      set_active?: boolean;
      active_model?: string;
    },
  ) => fetchJson(`/api/admin/llm-providers/${encodeURIComponent(provider)}`, { method: 'PUT', ...json(data) }),
  updateAgents: (data: { enabled: boolean }) =>
    fetchJson('/api/admin/agents', { method: 'PUT', ...json(data) }),
  getMcpServers: () => fetchJson<MCPServersResponse>('/api/admin/mcp-servers'),
  createMcpServer: (data: Record<string, unknown>) =>
    fetchJson('/api/admin/mcp-servers', { method: 'POST', ...json(data) }),
  updateMcpServer: (name: string, data: Record<string, unknown>) =>
    fetchJson(`/api/admin/mcp-servers/${encodeURIComponent(name)}`, { method: 'PUT', ...json(data) }),
  deleteMcpServer: (name: string) =>
    fetchJson(`/api/admin/mcp-servers/${encodeURIComponent(name)}`, { method: 'DELETE' }),
  testMcpServer: (name: string) =>
    fetchJson<MCPServerTestResponse>(`/api/admin/mcp-servers/${encodeURIComponent(name)}/test`, { method: 'POST' }),
  getMcpServerMethods: (name: string) =>
    fetchJson<MCPMethodsResponse>(`/api/admin/mcp-servers/${encodeURIComponent(name)}/methods`),

  // Agent Teams
  getAgentTeams: (enabledOnly = false) =>
    fetchJson<AgentTeam[]>(`/api/admin/agent-teams${enabledOnly ? '?enabled_only=true' : ''}`),
  getAgentTeamSources: () =>
    fetchJson<{ team_dirs: string[]; install_dir: string }>('/api/admin/agent-teams/sources'),
  installAgentTeam: (source: string, name?: string) =>
    fetchJson<Record<string, unknown>>('/api/admin/agent-teams/install', { method: 'POST', ...json({ source, name }) }),
  uninstallAgentTeam: (teamId: string) =>
    fetchJson<{ status: string; id: string }>(`/api/admin/agent-teams/install/${encodeURIComponent(teamId)}`, { method: 'DELETE' }),
  createAgentTeam: (data: AgentTeam) =>
    fetchJson<AgentTeam>('/api/admin/agent-teams', { method: 'POST', ...json(data) }),
  updateAgentTeam: (id: string, data: AgentTeam) =>
    fetchJson<AgentTeam>(`/api/admin/agent-teams/team/${id}`, { method: 'PUT', ...json(data) }),
  deleteAgentTeam: (id: string) =>
    fetchJson<{ status: string; id: string }>(`/api/admin/agent-teams/team/${id}`, { method: 'DELETE' }),
  buildAgentTeamFromPrompt: (prompt: string) =>
    fetchJson<AgentTeam>('/api/admin/agent-teams/build-from-prompt', { method: 'POST', ...json({ prompt }) }),
  testAgentTeamRoute: (task: string) =>
    fetchJson<Record<string, unknown>>('/api/admin/agent-teams/test-route', { method: 'POST', ...json({ task }) }),
  dryRunAgentTeam: (id: string, task: string) =>
    fetchJson<Record<string, unknown>>(`/api/admin/agent-teams/team/${id}/dry-run`, { method: 'POST', ...json({ task }) }),
  getAgentTeamRuns: (limit = 50) =>
    fetchJson<AgentTeamRun[]>(`/api/admin/agent-teams/runs?limit=${limit}`),
  getAgentTeamRun: (runId: string) =>
    fetchJson<Record<string, unknown>>(`/api/admin/agent-teams/runs/${encodeURIComponent(runId)}`),

  // Agent Skills
  getAgentSkills: (enabledOnly = false) =>
    fetchJson<AgentSkillItem[]>(`/api/admin/agent-skills${enabledOnly ? '?enabled_only=true' : ''}`),
  createAgentSkill: (data: Omit<AgentSkillItem, 'id' | 'created_at' | 'updated_at'>) =>
    fetchJson<AgentSkillItem>('/api/admin/agent-skills', { method: 'POST', ...json(data) }),
  updateAgentSkill: (id: number, data: Omit<AgentSkillItem, 'id' | 'created_at' | 'updated_at'>) =>
    fetchJson<AgentSkillItem>(`/api/admin/agent-skills/${id}`, { method: 'PUT', ...json(data) }),
  deleteAgentSkill: (id: number) =>
    fetchJson<{ status: string; id: number }>(`/api/admin/agent-skills/${id}`, { method: 'DELETE' }),

  // Logs
  getRecentLogs: (lines = 200, level = '') =>
    fetchJson<{ lines: string[]; file: string }>(`/api/logs/recent?lines=${lines}${level ? `&level=${level}` : ''}`),

  // Chat
  getChatHistory: () => fetchJson('/api/chat/history'),
  getChatAttachment: (path: string) =>
    fetchJson<Attachment>(`/api/chat/attachment?path=${encodeURIComponent(path)}`),
};

export function timeAgo(value: string | number): string {
  const date = typeof value === 'number' ? new Date(value * 1000) : new Date(value);
  const seconds = Math.floor((Date.now() - date.getTime()) / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

export function formatUptime(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}
