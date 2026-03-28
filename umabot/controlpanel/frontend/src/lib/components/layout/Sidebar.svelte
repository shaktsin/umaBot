<script lang="ts">
  import { appStore, type PanelId } from '$lib/stores/app.svelte';
  import {
    LayoutDashboard, MessageSquare, Plug, Wand2, Clock, Shield, Settings, FileText,
  } from 'lucide-svelte';

  const navGroups = [
    {
      label: 'Core',
      items: [
        { id: 'dashboard' as PanelId, label: 'Dashboard', icon: LayoutDashboard },
        { id: 'chat'      as PanelId, label: 'Chat',      icon: MessageSquare },
      ],
    },
    {
      label: 'Channels',
      items: [
        { id: 'connectors' as PanelId, label: 'Connectors', icon: Plug },
        { id: 'skills'     as PanelId, label: 'Skills',     icon: Wand2 },
        { id: 'tasks'      as PanelId, label: 'Tasks',      icon: Clock },
      ],
    },
    {
      label: 'System',
      items: [
        { id: 'policy' as PanelId, label: 'Policy', icon: Shield },
        { id: 'config' as PanelId, label: 'Config', icon: Settings },
        { id: 'logs'   as PanelId, label: 'Logs',   icon: FileText },
      ],
    },
  ];
</script>

<aside class="flex flex-col w-52 shrink-0 bg-zinc-900 border-r border-zinc-800">
  <!-- Logo -->
  <div class="flex items-center gap-2.5 px-4 h-12 border-b border-zinc-800">
    <div class="w-7 h-7 rounded-lg bg-violet-600 flex items-center justify-center shrink-0">
      <svg class="w-4 h-4 text-white" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09z" />
      </svg>
    </div>
    <span class="text-sm font-semibold text-zinc-100">umaBot</span>
  </div>

  <!-- Navigation -->
  <nav class="flex-1 overflow-y-auto py-3 px-2 space-y-4">
    {#each navGroups as group}
      <div>
        <p class="px-2 mb-1 text-[10px] font-semibold text-zinc-600 uppercase tracking-widest">{group.label}</p>
        {#each group.items as item}
          {@const active = appStore.activePanel === item.id}
          <button
            class="w-full flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-sm transition-colors relative
                   {active
                     ? 'bg-zinc-800 text-zinc-100 font-medium'
                     : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/60'}"
            onclick={() => appStore.navigate(item.id)}
          >
            {#if active}
              <span class="absolute left-0 top-1 bottom-1 w-0.5 bg-violet-500 rounded-full"></span>
            {/if}
            <item.icon class="w-4 h-4 shrink-0" />
            {item.label}
            {#if item.id === 'policy' && appStore.pendingCount > 0}
              <span class="ml-auto flex items-center justify-center w-5 h-5 rounded-full bg-amber-500/20 text-amber-400 text-[10px] font-bold">
                {appStore.pendingCount}
              </span>
            {/if}
          </button>
        {/each}
      </div>
    {/each}
  </nav>

  <!-- Version footer -->
  <div class="px-4 py-3 border-t border-zinc-800">
    <p class="text-[10px] text-zinc-600">Control Panel v0.1.0</p>
  </div>
</aside>
