<script lang="ts">
  import type { MultiAgentNode, MultiAgentRun } from '$lib/types';

  let { run }: { run: MultiAgentRun } = $props();

  type FlatNode = { node: MultiAgentNode; depth: number };

  let expanded = $state(true);
  let selectedNodeId = $state('');
  let selectedLogCount = $state(40);

  function statusTone(status: string): string {
    const s = (status || '').toLowerCase();
    if (s === 'completed') return 'bg-emerald-500/20 text-emerald-300 border-emerald-500/40';
    if (s === 'running') return 'bg-sky-500/20 text-sky-300 border-sky-500/40';
    if (s === 'failed' || s === 'cancelled') return 'bg-red-500/20 text-red-300 border-red-500/40';
    if (s === 'waiting_approval') return 'bg-amber-500/20 text-amber-300 border-amber-500/40';
    return 'bg-zinc-700/40 text-zinc-300 border-zinc-600';
  }

  function nodeStatusDot(status: string): string {
    const s = (status || '').toLowerCase();
    if (s === 'completed') return 'bg-emerald-400';
    if (s === 'running') return 'bg-sky-400 animate-pulse';
    if (s === 'failed' || s === 'cancelled') return 'bg-red-400';
    if (s === 'waiting_approval') return 'bg-amber-400';
    return 'bg-zinc-500';
  }

  function orderedNodes(currentRun: MultiAgentRun): FlatNode[] {
    const nodes = Object.values(currentRun.nodes || {});
    if (nodes.length === 0) return [];

    const byParent = new Map<string, MultiAgentNode[]>();
    for (const node of nodes) {
      const parent = node.parent_node_id || '';
      const arr = byParent.get(parent) || [];
      arr.push(node);
      byParent.set(parent, arr);
    }
    for (const arr of byParent.values()) {
      arr.sort((a, b) => a.node_id.localeCompare(b.node_id));
    }

    const out: FlatNode[] = [];
    const visited = new Set<string>();
    const roots: MultiAgentNode[] = [];
    const rootNode = currentRun.root_node_id ? currentRun.nodes[currentRun.root_node_id] : undefined;
    if (rootNode) roots.push(rootNode);
    for (const node of nodes) {
      if (!node.parent_node_id || !currentRun.nodes[node.parent_node_id]) {
        if (!roots.find((x) => x.node_id === node.node_id)) {
          roots.push(node);
        }
      }
    }

    const walk = (node: MultiAgentNode, depth: number) => {
      if (visited.has(node.node_id)) return;
      visited.add(node.node_id);
      out.push({ node, depth });
      const children = byParent.get(node.node_id) || [];
      for (const child of children) {
        walk(child, depth + 1);
      }
    };

    for (const root of roots) {
      walk(root, 0);
    }
    for (const node of nodes) {
      if (!visited.has(node.node_id)) walk(node, 0);
    }
    return out;
  }

  function formatTime(iso?: string): string {
    if (!iso) return '';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '';
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }

  function formatDuration(currentRun: MultiAgentRun): string {
    const start = new Date(currentRun.started_at).getTime();
    const end = currentRun.completed_at
      ? new Date(currentRun.completed_at).getTime()
      : Date.now();
    if (Number.isNaN(start) || Number.isNaN(end) || end < start) return '';
    const sec = Math.max(1, Math.floor((end - start) / 1000));
    if (sec < 60) return `${sec}s`;
    const min = Math.floor(sec / 60);
    const rem = sec % 60;
    return `${min}m ${rem}s`;
  }

  let flatNodes = $derived(orderedNodes(run));
  let selectedNode = $derived(
    selectedNodeId ? run.nodes[selectedNodeId] : (flatNodes[0]?.node ?? undefined),
  );
</script>

<div class="rounded-2xl border border-zinc-700/70 bg-zinc-900/80 p-3.5 space-y-3">
  <button
    class="w-full flex items-start justify-between gap-3 text-left"
    onclick={() => (expanded = !expanded)}
  >
    <div class="min-w-0">
      <div class="flex items-center gap-2 mb-1.5">
        <span class="text-[11px] uppercase tracking-wider text-zinc-500">Multi-agent run</span>
        <span class={`text-[11px] px-2 py-0.5 rounded-full border ${statusTone(run.status)}`}>
          {run.status || 'running'}
        </span>
      </div>
      <p class="text-sm text-zinc-200 leading-relaxed break-words">
        {run.task || 'Task in progress'}
      </p>
      <p class="text-[11px] text-zinc-500 mt-1">
        run: {run.run_id} · started {formatTime(run.started_at)} · elapsed {formatDuration(run)}
      </p>
      {#if run.team_name || run.team_id || run.selected_by || run.fit_reason}
        <p class="text-[11px] text-zinc-500 mt-1 break-words">
          {#if run.fit_passed !== undefined}
            fit: <span class={run.fit_passed ? 'text-emerald-300' : 'text-amber-300'}>{run.fit_passed ? 'pass' : 'reject'}</span>
          {/if}
          {#if run.fit_reason}
            {run.fit_passed !== undefined ? ' · ' : ''}reason: {run.fit_reason}
          {/if}
          {#if run.team_name || run.team_id}
            {' · '}team: {run.team_name || run.team_id}
          {/if}
          {#if run.selected_by}
            {' · '}route: {run.selected_by}
          {/if}
          {#if run.route_score !== undefined && run.route_threshold !== undefined}
            {' · '}score: {run.route_score} / {run.route_threshold}
          {/if}
          {#if run.complexity_class}
            {' · '}complexity: {run.complexity_class}
          {/if}
        </p>
      {/if}
    </div>
    <svg
      class={`w-4 h-4 text-zinc-500 shrink-0 mt-1 transition-transform ${expanded ? 'rotate-180' : ''}`}
      fill="none"
      stroke="currentColor"
      stroke-width="2"
      viewBox="0 0 24 24"
    >
      <path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7" />
    </svg>
  </button>

  {#if expanded}
    <div class="grid grid-cols-1 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)] gap-3">
      <div class="rounded-xl border border-zinc-700 bg-zinc-950/70 p-2.5">
        <p class="text-[11px] uppercase tracking-wider text-zinc-500 mb-2">Topology</p>
        <div class="space-y-1">
          {#if flatNodes.length === 0}
            <p class="text-xs text-zinc-500">Waiting for nodes…</p>
          {:else}
            {#each flatNodes as row}
              <button
                class={`w-full flex items-center gap-2 rounded-lg px-2 py-1.5 text-left text-xs transition-colors ${
                  selectedNode?.node_id === row.node.node_id
                    ? 'bg-violet-500/15 border border-violet-500/30 text-zinc-100'
                    : 'hover:bg-zinc-800 border border-transparent text-zinc-300'
                }`}
                style={`padding-left: ${8 + Math.min(row.depth, 8) * 16}px;`}
                onclick={() =>
                  (selectedNodeId =
                    selectedNode?.node_id === row.node.node_id ? '' : row.node.node_id)}
              >
                <span class={`w-2 h-2 rounded-full shrink-0 ${nodeStatusDot(row.node.status)}`}></span>
                <span class="truncate">{row.node.role || row.node.node_id}</span>
                <span class="ml-auto text-[10px] text-zinc-500">{row.node.status}</span>
              </button>
            {/each}
          {/if}
        </div>
      </div>

      <div class="rounded-xl border border-zinc-700 bg-zinc-950/70 p-2.5 min-h-40">
        <p class="text-[11px] uppercase tracking-wider text-zinc-500 mb-2">Node detail</p>
        {#if selectedNode}
          <div class="space-y-2">
            <div>
              <p class="text-sm text-zinc-200">{selectedNode.role || selectedNode.node_id}</p>
              <p class="text-[11px] text-zinc-500">
                node: {selectedNode.node_id} · parent: {selectedNode.parent_node_id || 'none'}
              </p>
            </div>
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-2 text-[11px]">
              <p class="text-zinc-400">status: <span class="text-zinc-200">{selectedNode.status}</span></p>
              <p class="text-zinc-400">model: <span class="text-zinc-200">{selectedNode.model || 'n/a'}</span></p>
              <p class="text-zinc-400">workspace: <span class="text-zinc-200">{selectedNode.workspace || 'n/a'}</span></p>
            </div>
            {#if selectedNode.objective}
              <p class="text-xs text-zinc-300 rounded-lg border border-zinc-700 bg-zinc-900/80 p-2">
                {selectedNode.objective}
              </p>
            {/if}

            <div class="flex items-center justify-between pt-1">
              <p class="text-[11px] uppercase tracking-wider text-zinc-500">Logs ({selectedNode.logs.length})</p>
              <select class="input py-1 text-[11px]" bind:value={selectedLogCount}>
                <option value={20}>Last 20</option>
                <option value={40}>Last 40</option>
                <option value={80}>Last 80</option>
              </select>
            </div>

            <div class="max-h-56 overflow-auto rounded-lg border border-zinc-700 bg-zinc-900/80 p-2 space-y-1.5">
              {#if selectedNode.logs.length === 0}
                <p class="text-xs text-zinc-500">No logs yet.</p>
              {:else}
                {#each selectedNode.logs.slice(-selectedLogCount) as log}
                  <div class="text-[11px] leading-relaxed">
                    <p class="text-zinc-400">
                      <span class="text-zinc-500">{formatTime(log.timestamp)}</span>
                      <span class="mx-1">·</span>
                      <span class="text-zinc-300">{log.event_type}</span>
                    </p>
                    <p class="text-zinc-200 break-words">{log.message}</p>
                  </div>
                {/each}
              {/if}
            </div>
          </div>
        {:else}
          <p class="text-xs text-zinc-500">Select a node to view details.</p>
        {/if}
      </div>
    </div>

    {#if run.summary}
      <div class="rounded-xl border border-zinc-700 bg-zinc-950/70 p-2.5 text-xs text-zinc-300 whitespace-pre-wrap break-words">
        {run.summary}
      </div>
    {/if}
  {/if}
</div>
