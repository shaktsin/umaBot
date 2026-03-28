<script lang="ts">
  import { api, timeAgo } from '$lib/api';
  import type { Connector } from '$lib/types';
  import StatusPill from '$lib/components/ui/StatusPill.svelte';
  import Badge from '$lib/components/ui/Badge.svelte';
  import { RefreshCw } from 'lucide-svelte';

  let connectors = $state<Connector[]>([]);
  let loading = $state(true);

  async function load() {
    try {
      connectors = await api.getConnectors();
    } finally {
      loading = false;
    }
  }

  $effect(() => { load(); });
</script>

<div class="space-y-4">
  <div class="flex items-center justify-between">
    <h1 class="section-title mb-0">Connectors</h1>
    <button class="btn-ghost" onclick={load}><RefreshCw class="w-3.5 h-3.5" /> Refresh</button>
  </div>

  {#if loading}
    <div class="space-y-2">
      {#each Array(3) as _}
        <div class="card h-20 animate-pulse"></div>
      {/each}
    </div>
  {:else if connectors.length === 0}
    <div class="card p-8 text-center text-zinc-500 text-sm">No connectors configured</div>
  {:else}
    <div class="space-y-3">
      {#each connectors as conn}
        <div class="card p-4">
          <div class="flex items-start justify-between gap-4">
            <div class="flex items-start gap-3">
              <StatusPill status={conn.status} />
              <div>
                <p class="text-sm font-semibold text-zinc-100">{conn.name}</p>
                <p class="text-xs text-zinc-500 mt-0.5">{conn.type}</p>
              </div>
            </div>
            <div class="flex items-center gap-2 shrink-0">
              <Badge variant={conn.mode === 'control' ? 'accent' : 'default'}>{conn.mode}</Badge>
              <span class="text-xs text-zinc-600">{conn.updated_at ? timeAgo(conn.updated_at) : 'never'}</span>
            </div>
          </div>
        </div>
      {/each}
    </div>
  {/if}
</div>
