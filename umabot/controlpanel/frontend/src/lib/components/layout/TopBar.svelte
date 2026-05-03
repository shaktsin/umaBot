<script lang="ts">
  import { appStore } from '$lib/stores/app.svelte';
  import { wsStore } from '$lib/ws.svelte';
  import { Bell } from 'lucide-svelte';

  const panelLabels: Record<string, string> = {
    dashboard: 'Dashboard',
    chat:      'Chat',
    connectors: 'Connectors',
    skills:    'Skills',
    tasks:     'Tasks',
    agent_teams: 'Agent Teams',
    providers: 'LLM Providers',
    mcp:       'MCP Servers',
    policy:    'Policy & Confirmations',
    config:    'Configuration',
    logs:      'Logs',
  };
</script>

<header class="flex items-center h-12 px-6 border-b border-zinc-800 bg-zinc-900 shrink-0">
  <h1 class="text-sm font-medium text-zinc-300">{panelLabels[appStore.activePanel] ?? ''}</h1>

  <div class="ml-auto flex items-center gap-3">
    <!-- Gateway status -->
    <div class="flex items-center gap-1.5 text-xs">
      {#if wsStore.connected && appStore.gatewayConnected}
        <span class="relative flex h-2 w-2">
          <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
          <span class="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
        </span>
        <span class="text-emerald-400">Gateway live</span>
      {:else if wsStore.connected}
        <span class="h-2 w-2 rounded-full bg-amber-500"></span>
        <span class="text-amber-400">Gateway offline</span>
      {:else}
        <span class="h-2 w-2 rounded-full bg-zinc-600"></span>
        <span class="text-zinc-500">Connecting…</span>
      {/if}
    </div>

    <!-- Pending confirmations bell -->
    <button
      class="relative p-1.5 rounded-lg text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 transition-colors"
      onclick={() => appStore.navigate('policy')}
      aria-label="Policy alerts"
    >
      <Bell class="w-4 h-4" />
      {#if appStore.pendingCount > 0}
        <span class="absolute -top-0.5 -right-0.5 flex items-center justify-center w-4 h-4
                     rounded-full bg-amber-500 text-[9px] font-bold text-zinc-950">
          {appStore.pendingCount}
        </span>
      {/if}
    </button>
  </div>
</header>
