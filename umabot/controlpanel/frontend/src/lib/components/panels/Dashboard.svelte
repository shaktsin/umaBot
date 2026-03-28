<script lang="ts">
  import { api, timeAgo, formatUptime } from '$lib/api';
  import type { StatusResponse, StatsResponse, AuditEntry } from '$lib/types';
  import StatCard from '$lib/components/ui/StatCard.svelte';
  import StatusPill from '$lib/components/ui/StatusPill.svelte';
  import { RefreshCw } from 'lucide-svelte';

  let status = $state<StatusResponse | null>(null);
  let stats = $state<StatsResponse | null>(null);
  let activity = $state<AuditEntry[]>([]);
  let loading = $state(true);
  let refreshing = $state(false);

  async function load() {
    try {
      [status, stats, activity] = await Promise.all([
        api.getStatus(),
        api.getStats(),
        api.getActivity(20),
      ]);
    } finally {
      loading = false;
      refreshing = false;
    }
  }

  function refresh() {
    refreshing = true;
    load();
  }

  $effect(() => {
    load();
    const t = setInterval(load, 8000);
    return () => clearInterval(t);
  });
</script>

<div class="space-y-6">
  <div class="flex items-center justify-between">
    <h1 class="section-title mb-0">Dashboard</h1>
    <button class="btn-ghost gap-1.5" onclick={refresh} disabled={refreshing}>
      <RefreshCw class="w-3.5 h-3.5 {refreshing ? 'animate-spin' : ''}" />
      Refresh
    </button>
  </div>

  {#if loading}
    <div class="grid grid-cols-2 lg:grid-cols-4 gap-4">
      {#each Array(4) as _}
        <div class="card p-5 h-24 animate-pulse bg-zinc-900"></div>
      {/each}
    </div>
  {:else if stats}
    <!-- Stat cards -->
    <div class="grid grid-cols-2 lg:grid-cols-4 gap-4">
      <StatCard label="Messages (24h)" value={stats.messages_24h} sub="{stats.messages_1h} in last hour" />
      <StatCard label="Connectors" value="{stats.connectors_active}/{stats.connectors_total}" sub="active" accent />
      <StatCard label="Pending" value={stats.pending_confirmations} sub="confirmations" warn />
      <StatCard label="Skills" value={stats.skills_loaded} sub="loaded" />
    </div>

    <!-- Uptime card -->
    {#if status}
      <div class="card px-5 py-4 flex items-center gap-4">
        <div class="flex items-center gap-2">
          <div class="flex items-center gap-1.5">
            {#if status.gateway_connected}
              <span class="relative flex h-2 w-2">
                <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                <span class="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
              </span>
              <span class="text-sm text-emerald-400 font-medium">Gateway running</span>
            {:else}
              <span class="h-2 w-2 rounded-full bg-red-500"></span>
              <span class="text-sm text-red-400 font-medium">Gateway offline</span>
            {/if}
          </div>
        </div>
        <div class="h-4 w-px bg-zinc-800"></div>
        <span class="text-sm text-zinc-500">Panel uptime: <span class="text-zinc-300">{formatUptime(status.uptime_seconds)}</span></span>
        {#if stats.active_tasks > 0}
          <div class="h-4 w-px bg-zinc-800"></div>
          <span class="text-sm text-zinc-500">{stats.active_tasks} active task{stats.active_tasks !== 1 ? 's' : ''}</span>
        {/if}
      </div>
    {/if}
  {/if}

  <!-- Connector health -->
  {#if status?.connectors && status.connectors.length > 0}
    <div>
      <h2 class="text-sm font-semibold text-zinc-400 mb-2 uppercase tracking-wider text-xs">Connectors</h2>
      <div class="card divide-y divide-zinc-800">
        {#each status.connectors as conn}
          <div class="flex items-center justify-between px-4 py-3">
            <div class="flex items-center gap-3">
              <StatusPill status={conn.status} />
              <div>
                <p class="text-sm font-medium text-zinc-200">{conn.name}</p>
                <p class="text-xs text-zinc-500">{conn.type}</p>
              </div>
            </div>
            <span class="text-xs text-zinc-600">
              {conn.updated_at ? timeAgo(conn.updated_at) : '—'}
            </span>
          </div>
        {/each}
      </div>
    </div>
  {/if}

  <!-- Activity feed -->
  {#if activity.length > 0}
    <div>
      <h2 class="text-sm font-semibold text-zinc-400 mb-2 uppercase tracking-wider text-xs">Recent Activity</h2>
      <div class="card divide-y divide-zinc-800">
        {#each activity as item}
          <div class="flex items-start gap-3 px-4 py-2.5">
            <div class="w-1.5 h-1.5 rounded-full bg-violet-500 mt-1.5 shrink-0"></div>
            <div class="flex-1 min-w-0">
              <span class="text-xs font-medium text-zinc-300">{item.event_type.replace(/_/g, ' ')}</span>
              {#if item.details && Object.keys(item.details).length > 0}
                <span class="text-xs text-zinc-600 ml-2">{Object.values(item.details).filter(Boolean).join(' · ')}</span>
              {/if}
            </div>
            <span class="text-xs text-zinc-600 shrink-0">{timeAgo(item.created_at)}</span>
          </div>
        {/each}
      </div>
    </div>
  {/if}
</div>
