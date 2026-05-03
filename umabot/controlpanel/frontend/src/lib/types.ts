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

export interface LLMProviderInfo {
  name: string;
  label: string;
  enabled: boolean;
  models: string[];
  default_model: string;
  api_key: string;
  api_key_configured: boolean;
  active: boolean;
}

export interface LLMProvidersResponse {
  active_provider: string;
  active_model: string;
  agents_enabled: boolean;
  providers: LLMProviderInfo[];
}

export interface MCPServerInfo {
  name: string;
  transport: string;
  command: string;
  args: string[];
  env: Record<string, string>;
  env_vars: string[];
  cwd: string;
  url: string;
  bearer_token_env_var: string;
  http_headers: Record<string, string>;
  env_http_headers: Record<string, string>;
  enabled: boolean;
  required: boolean;
  startup_timeout_sec: number;
  tool_timeout_sec: number;
  enabled_tools: string[];
  disabled_tools: string[];
  mcp_oauth_callback_port: number;
  mcp_oauth_callback_url: string;
}

export interface MCPServersResponse {
  servers: MCPServerInfo[];
}

export interface MCPMethod {
  name: string;
  prefixed_name: string;
  description: string;
  schema: Record<string, unknown>;
}

export interface MCPMethodsResponse {
  server: string;
  discovered: boolean;
  methods: MCPMethod[];
  error?: string;
}

export interface MCPServerTestResponse {
  server: string;
  ok: boolean;
  method_count: number;
  methods: MCPMethod[];
  error?: string;
}

export interface MultiAgentApprovalRequest {
  token: string;
  run_id: string;
  reason: string;
  action_summary: string;
  requested_at: string;
}

export interface MultiAgentNodeLog {
  timestamp: string;
  event_type: string;
  message: string;
  payload?: Record<string, unknown>;
}

export interface MultiAgentNode {
  node_id: string;
  parent_node_id: string;
  role: string;
  status: string;
  objective?: string;
  workspace?: string;
  model?: string;
  logs: MultiAgentNodeLog[];
}

export interface MultiAgentRun {
  run_id: string;
  task: string;
  status: string;
  started_at: string;
  completed_at?: string;
  summary?: string;
  root_node_id: string;
  nodes: Record<string, MultiAgentNode>;
  fit_passed?: boolean;
  fit_reason?: string;
  complexity_class?: string;
  selected_by?: string;
  route_score?: number;
  route_threshold?: number;
  team_id?: string;
  team_name?: string;
}

export interface AgentTeamRoute {
  id?: number;
  route_type: string;
  pattern_or_hint: string;
  weight: number;
}

export interface AgentTeamMember {
  id?: number;
  role: string;
  objective_template: string;
  output_schema: Record<string, unknown>;
  model: string;
  tool_allowlist: string[];
  skill_allowlist: string[];
  workspace: string;
  order_index: number;
  max_tool_calls: number;
  max_iterations: number;
  effective_tools?: string[];
}

export interface AgentTeam {
  id?: string;
  name: string;
  description: string;
  enabled: boolean;
  priority: number;
  team_type: 'chain' | 'parallel' | 'orchestrator_worker' | 'hybrid';
  confidence_threshold: number;
  fit_policy: Record<string, unknown>;
  budget_policy: Record<string, unknown>;
  retry_policy: Record<string, unknown>;
  tool_pool: string[];
  required_capabilities: string[];
  capability_overrides: Record<string, string[]>;
  members: AgentTeamMember[];
  routes: AgentTeamRoute[];
  rules_markdown?: string;
  worksteps_markdown?: string;
  source_dir?: string;
  writable?: boolean;
  created_at?: string;
  updated_at?: string;
}

export interface AgentSkillItem {
  id?: number;
  skill_key: string;
  name: string;
  description: string;
  version: string;
  required_tools: string[];
  prompt_template: string;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface AgentTeamRun {
  id: number;
  team_id: string | number | null;
  run_id: string;
  status: string;
  complexity_class: string;
  selected_by: string;
  budget_snapshot: Record<string, unknown>;
  route_rationale: Record<string, unknown>;
  started_at: string;
  completed_at: string | null;
}
