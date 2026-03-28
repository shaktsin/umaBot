export interface Connector {
  name: string;
  type: string;
  status: 'connected' | 'disconnected' | 'connecting' | 'error' | 'unknown';
  mode: string;
  channel: string;
  updated_at: string | null;
}

export interface Skill {
  name: string;
  description: string;
  source_dir: string;
  license: string | null;
  metadata: Record<string, unknown>;
}

export interface Task {
  id: number;
  name: string;
  prompt: string;
  task_type: string;
  status: string;
  schedule: Record<string, unknown>;
  timezone: string;
  next_run_at: string | null;
  last_run_at: string | null;
  last_result: string | null;
  last_error: string | null;
  created_at: string;
}

export interface PendingConfirmation {
  token: string;
  tool_name: string;
  args_preview: string;
  message: string;
  chat_id: string;
  requested_at: number;
}

export interface AuditEntry {
  event_type: string;
  details: Record<string, unknown>;
  created_at: string;
}

export interface Attachment {
  filename: string;
  mime_type: string;
  data: string; // base64-encoded
}

export interface ChatMessage {
  id?: number;
  role: 'user' | 'assistant';
  content: string;
  created_at?: string;
  tool_calls?: ToolCall[];
  attachments?: Attachment[];
  pending?: boolean;
}

export interface ToolCall {
  tool_name: string;
  args: Record<string, unknown>;
  result: unknown;
  created_at?: string;
}

export interface StatusResponse {
  gateway_connected: boolean;
  uptime_seconds: number;
  pending_confirmations: number;
  connectors: Connector[];
}

export interface StatsResponse {
  messages_1h: number;
  messages_24h: number;
  skills_loaded: number;
  active_tasks: number;
  pending_confirmations: number;
  connectors_total: number;
  connectors_active: number;
}
