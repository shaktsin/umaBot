import type {
  AuditEntry,
  Connector,
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
  getAudit: (limit = 100, event_type = '') =>
    fetchJson<AuditEntry[]>(`/api/policy/audit?limit=${limit}${event_type ? `&event_type=${event_type}` : ''}`),
  getPolicySettings: () => fetchJson<{ confirmation_strictness: string; shell_enabled: boolean }>('/api/policy/settings'),

  // Config
  getConfig: () => fetchJson<Record<string, unknown>>('/api/config'),
  updateConfig: (data: Record<string, unknown>) =>
    fetchJson('/api/config', { method: 'PUT', ...json(data) }),
  getRawYaml: () => fetchJson<{ yaml: string; path: string }>('/api/config/raw'),

  // Logs
  getRecentLogs: (lines = 200, level = '') =>
    fetchJson<{ lines: string[]; file: string }>(`/api/logs/recent?lines=${lines}${level ? `&level=${level}` : ''}`),

  // Chat
  getChatHistory: () => fetchJson('/api/chat/history'),
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
