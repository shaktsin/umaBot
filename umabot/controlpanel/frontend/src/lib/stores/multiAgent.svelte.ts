import type { MultiAgentApprovalRequest, MultiAgentNode, MultiAgentNodeLog, MultiAgentRun } from '$lib/types';

type AnyRecord = Record<string, unknown>;

const MAX_RUNS = 20;
const MAX_NODE_LOGS = 200;

function asRecord(value: unknown): AnyRecord | null {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    return value as AnyRecord;
  }
  return null;
}

function asString(value: unknown, fallback = ''): string {
  if (typeof value === 'string') return value;
  if (typeof value === 'number') return String(value);
  return fallback;
}

function asObjectArray(value: unknown): AnyRecord[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => asRecord(item))
    .filter((item): item is AnyRecord => item !== null);
}

function asBoolean(value: unknown, fallback = false): boolean {
  if (typeof value === 'boolean') return value;
  return fallback;
}

function asNumber(value: unknown, fallback = 0): number {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string') {
    const n = Number(value);
    if (Number.isFinite(n)) return n;
  }
  return fallback;
}

type PendingDecision = {
  fit_passed?: boolean;
  fit_reason?: string;
  complexity_class?: string;
  selected_by?: string;
  route_score?: number;
  route_threshold?: number;
  team_id?: string;
  team_name?: string;
};

class MultiAgentStore {
  runs = $state<MultiAgentRun[]>([]);
  version = $state(0);
  pendingApprovals = $state<MultiAgentApprovalRequest[]>([]);
  private pendingDecision: PendingDecision | null = null;

  handleEvent(name: string, rawData: unknown) {
    const data = asRecord(rawData) ?? {};
    if (name.startsWith('team.')) {
      this.handleTeamEvent(name, data);
      return;
    }
    if (!name.startsWith('multi_agent_')) return;

    if (name === 'multi_agent_run_started') {
      this.applyRunStarted(data);
      return;
    }
    if (name === 'multi_agent_node_added') {
      this.applyNodeAdded(data);
      return;
    }
    if (name === 'multi_agent_node_status') {
      this.applyNodeStatus(data);
      return;
    }
    if (name === 'multi_agent_node_log') {
      this.applyNodeLog(data);
      return;
    }
    if (name === 'multi_agent_run_completed') {
      this.applyRunCompleted(data);
      return;
    }
    if (name === 'multi_agent_approval_requested') {
      this.applyApprovalRequested(data);
      return;
    }
    if (name === 'multi_agent_approval_resolved') {
      this.removeApproval(asString(data.token));
    }
  }

  private handleTeamEvent(name: string, data: AnyRecord) {
    if (name === 'team.fit.started' || name === 'team.fit.rejected') {
      this.pendingDecision = {
        ...(this.pendingDecision || {}),
        fit_passed: asBoolean(data.passed, name === 'team.fit.started'),
        fit_reason: asString(data.reason),
        complexity_class: asString(data.complexity_class),
      };
      return;
    }
    if (name === 'team.route.selected' || name === 'team.route.not_selected') {
      this.pendingDecision = {
        ...(this.pendingDecision || {}),
        selected_by: asString(data.selected_by),
        route_score: asNumber(data.score),
        route_threshold: asNumber(data.threshold),
        team_id: asString(data.team_id),
        team_name: asString(data.team_name),
      };
      return;
    }
    if (name === 'team.exec.started') {
      this.applyTeamExecStarted(data);
      return;
    }
    if (name === 'team.exec.completed') {
      this.applyTeamExecCompleted(data);
      return;
    }
    if (name === 'team.member.started') {
      this.applyTeamMemberStatus(data, 'running');
      return;
    }
    if (name === 'team.member.completed') {
      this.applyTeamMemberStatus(data, 'completed');
      return;
    }
    if (name === 'team.member.failed') {
      this.applyTeamMemberStatus(data, 'failed');
    }
  }

  private applyRunStarted(data: AnyRecord) {
    const runId = asString(data.run_id);
    if (!runId) return;

    const run = this.ensureRun(runId);
    run.task = asString(data.task, run.task);
    run.status = asString(data.status, run.status || 'running');
    run.started_at = asString(data.started_at, run.started_at || new Date().toISOString());
    run.root_node_id = asString(data.root_node_id, run.root_node_id);
    run.completed_at = undefined;
    run.summary = undefined;
    if (this.pendingDecision) {
      this.applyDecisionToRun(run, this.pendingDecision);
    }

    const nodes = asObjectArray(data.nodes);
    for (const nodeData of nodes) {
      const node = this.ensureNode(
        run,
        asString(nodeData.node_id),
        asString(nodeData.parent_node_id),
      );
      this.patchNode(node, nodeData);
    }
    this.touch();
  }

  private applyNodeAdded(data: AnyRecord) {
    const runId = asString(data.run_id);
    const nodeId = asString(data.node_id);
    if (!runId || !nodeId) return;

    const run = this.ensureRun(runId);
    const node = this.ensureNode(run, nodeId, asString(data.parent_node_id));
    this.patchNode(node, data);
    this.touch();
  }

  private applyNodeStatus(data: AnyRecord) {
    const runId = asString(data.run_id);
    const nodeId = asString(data.node_id);
    if (!runId || !nodeId) return;

    const run = this.ensureRun(runId);
    const node = this.ensureNode(run, nodeId, asString(data.parent_node_id));
    this.patchNode(node, data);
    this.touch();
  }

  private applyNodeLog(data: AnyRecord) {
    const runId = asString(data.run_id);
    const nodeId = asString(data.node_id);
    if (!runId || !nodeId) return;

    const run = this.ensureRun(runId);
    const node = this.ensureNode(run, nodeId, asString(data.parent_node_id));
    if (!node.status || node.status === 'queued') {
      node.status = 'running';
    }

    const log: MultiAgentNodeLog = {
      timestamp: asString(data.timestamp, new Date().toISOString()),
      event_type: asString(data.event_type, 'log'),
      message: asString(data.message, ''),
      payload: asRecord(data.payload) ?? {},
    };
    node.logs = [...node.logs, log].slice(-MAX_NODE_LOGS);
    this.touch();
  }

  private applyRunCompleted(data: AnyRecord) {
    const runId = asString(data.run_id);
    if (!runId) return;

    const run = this.ensureRun(runId);
    run.status = asString(data.status, 'completed');
    run.summary = asString(data.summary, run.summary);
    run.completed_at = asString(data.completed_at, new Date().toISOString());
    this.touch();
  }

  private applyApprovalRequested(data: AnyRecord) {
    const token = asString(data.token);
    if (!token || this.pendingApprovals.find((a) => a.token === token)) return;
    const approval: MultiAgentApprovalRequest = {
      token,
      run_id: asString(data.run_id),
      reason: asString(data.reason),
      action_summary: asString(data.action_summary),
      requested_at: asString(data.timestamp, new Date().toISOString()),
    };
    this.pendingApprovals = [...this.pendingApprovals, approval];
    this.touch();
  }

  removeApproval(token: string) {
    this.pendingApprovals = this.pendingApprovals.filter((a) => a.token !== token);
    this.touch();
  }

  private applyTeamExecStarted(data: AnyRecord) {
    const runId = asString(data.run_id);
    if (!runId) return;

    const run = this.ensureRun(runId);
    run.status = 'running';
    run.started_at = asString(data.timestamp, run.started_at || new Date().toISOString());
    run.team_id = asString(data.team_id, run.team_id || '');
    run.team_name = asString(data.team_name, run.team_name || '');
    run.complexity_class = asString(data.complexity_class, run.complexity_class || '');
    if (this.pendingDecision) {
      this.applyDecisionToRun(run, this.pendingDecision);
      this.pendingDecision = null;
    }
    this.touch();
  }

  private applyTeamExecCompleted(data: AnyRecord) {
    const runId = asString(data.run_id);
    if (!runId) return;
    const run = this.ensureRun(runId);
    run.status = asString(data.status, 'completed');
    run.completed_at = asString(data.timestamp, new Date().toISOString());
    this.touch();
  }

  private applyTeamMemberStatus(data: AnyRecord, status: string) {
    const runId = asString(data.run_id);
    const nodeId = asString(data.node_id);
    if (!runId || !nodeId) return;
    const run = this.ensureRun(runId);
    const node = this.ensureNode(run, nodeId, asString(data.parent_node_id));
    node.status = status;
    node.role = asString(data.role, node.role || nodeId);

    const maybeError = asString(data.error);
    const message = maybeError ? `${status}: ${maybeError}` : status;
    node.logs = [
      ...node.logs,
      {
        timestamp: asString(data.timestamp, new Date().toISOString()),
        event_type: `team.member.${status}`,
        message,
        payload: data,
      },
    ].slice(-MAX_NODE_LOGS);
    this.touch();
  }

  private applyDecisionToRun(run: MultiAgentRun, decision: PendingDecision) {
    if (decision.fit_passed !== undefined) run.fit_passed = decision.fit_passed;
    if (decision.fit_reason) run.fit_reason = decision.fit_reason;
    if (decision.complexity_class) run.complexity_class = decision.complexity_class;
    if (decision.selected_by) run.selected_by = decision.selected_by;
    if (decision.route_score !== undefined) run.route_score = decision.route_score;
    if (decision.route_threshold !== undefined) run.route_threshold = decision.route_threshold;
    if (decision.team_id) run.team_id = decision.team_id;
    if (decision.team_name) run.team_name = decision.team_name;
  }

  private ensureRun(runId: string): MultiAgentRun {
    const existing = this.runs.find((run) => run.run_id === runId);
    if (existing) return existing;

    const run: MultiAgentRun = {
      run_id: runId,
      task: '',
      status: 'running',
      started_at: new Date().toISOString(),
      root_node_id: '',
      nodes: {},
    };
    this.runs = [run, ...this.runs].slice(0, MAX_RUNS);
    return run;
  }

  private ensureNode(run: MultiAgentRun, nodeId: string, parentNodeId = ''): MultiAgentNode {
    const existing = run.nodes[nodeId];
    if (existing) {
      if (!existing.parent_node_id && parentNodeId) {
        existing.parent_node_id = parentNodeId;
      }
      return existing;
    }
    const node: MultiAgentNode = {
      node_id: nodeId,
      parent_node_id: parentNodeId,
      role: 'agent',
      status: 'queued',
      logs: [],
    };
    run.nodes[nodeId] = node;
    return node;
  }

  private patchNode(node: MultiAgentNode, data: AnyRecord) {
    const status = asString(data.status);
    const role = asString(data.role);
    const objective = asString(data.objective);
    const workspace = asString(data.workspace);
    const model = asString(data.model);
    const parent = asString(data.parent_node_id);

    if (status) node.status = status;
    if (role) node.role = role;
    if (objective) node.objective = objective;
    if (workspace) node.workspace = workspace;
    if (model) node.model = model;
    if (parent) node.parent_node_id = parent;
  }

  private touch() {
    this.version += 1;
    this.runs = [...this.runs];
  }
}

export const multiAgentStore = new MultiAgentStore();
