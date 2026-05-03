<script lang="ts">
  import { api, timeAgo } from '$lib/api';
  import type { AuditEntry, PendingConfirmation } from '$lib/types';
  import { wsStore } from '$lib/ws.svelte';
  import { appStore } from '$lib/stores/app.svelte';
  import Badge from '$lib/components/ui/Badge.svelte';
  import { RefreshCw, ShieldCheck, ShieldX } from 'lucide-svelte';

  let pending = $state<PendingConfirmation[]>([]);
  let audit = $state<AuditEntry[]>([]);
  let settings = $state<{
    confirmation_strictness: string;
    shell_enabled: boolean;
    approval_mode: string;
    auto_approve_workspaces: string[];
    auto_approve_tools: string[];
    auto_approve_shell_commands: string[];
  } | null>(null);
  let loading = $state(true);
  let confirming = $state<string | null>(null);

  async function load() {
    [pending, audit, settings] = await Promise.all([
      api.getPending(),
      api.getAudit(50),
      api.getPolicySettings(),
    ]);
    loading = false;
  }

  // Merge live WS pending with DB state
  $effect(() => {
    const wsPending = wsStore.pendingConfirmations;
    if (wsPending.length > 0) {
      // Merge: add WS confirmations not already in pending list
      const existingTokens = new Set(pending.map((p) => p.token));
      const newOnes = wsPending.filter((p) => !existingTokens.has(p.token));
      if (newOnes.length > 0) {
        pending = [...newOnes, ...pending];
        appStore.pendingCount = pending.length;
      }
    }
  });

  async function confirm(token: string, approved: boolean) {
    confirming = token;
    try {
      await api.confirmAction(token, approved);
      pending = pending.filter((p) => p.token !== token);
      appStore.pendingCount = pending.length;
      await api.getAudit(50).then(d => { audit = d; });
    } finally {
      confirming = null;
    }
  }

  $effect(() => { load(); });
</script>

<div class="space-y-6">
  <div class="flex items-center justify-between">
    <h1 class="section-title mb-0">Policy & Confirmations</h1>
    <button class="btn-ghost" onclick={load}><RefreshCw class="w-3.5 h-3.5" /> Refresh</button>
  </div>

  <!-- Settings -->
  {#if settings}
    <div class="card p-4 flex items-center gap-6">
      <div class="flex items-center gap-2">
        <span class="label">Strictness</span>
        <Badge variant={settings.confirmation_strictness === 'strict' ? 'warning' : 'default'}>
          {settings.confirmation_strictness}
        </Badge>
      </div>
      <div class="flex items-center gap-2">
        <span class="label">Shell tools</span>
        <Badge variant={settings.shell_enabled ? 'warning' : 'default'}>
          {settings.shell_enabled ? 'enabled' : 'disabled'}
        </Badge>
      </div>
      <div class="flex items-center gap-2">
        <span class="label">Approval mode</span>
        <Badge variant={settings.approval_mode === 'auto_approve_workspace' ? 'warning' : 'default'}>
          {settings.approval_mode}
        </Badge>
      </div>
    </div>
  {/if}

  <!-- Pending confirmations -->
  <div>
    <div class="flex items-center gap-2 mb-3">
      <h2 class="text-sm font-semibold text-zinc-300">Pending Confirmations</h2>
      {#if pending.length > 0}
        <span class="flex items-center justify-center w-5 h-5 rounded-full bg-amber-500/20 text-amber-400 text-[10px] font-bold">
          {pending.length}
        </span>
      {/if}
    </div>

    {#if loading}
      <div class="card h-20 animate-pulse"></div>
    {:else if pending.length === 0}
      <div class="card p-6 text-center text-zinc-600 text-sm">No pending confirmations</div>
    {:else}
      <div class="space-y-3">
        {#each pending as c}
          <div class="card overflow-hidden border-amber-500/20">
            <div class="flex items-center justify-between px-4 py-3 bg-amber-500/5 border-b border-amber-500/10">
              <div class="flex items-center gap-2">
                <span class="text-amber-400">⚠</span>
                <code class="font-mono text-sm text-zinc-200 font-medium">{c.tool_name}</code>
              </div>
              <span class="text-xs text-zinc-500">{timeAgo(c.requested_at * 1000)}</span>
            </div>
            <div class="p-4">
              <pre class="text-xs font-mono text-zinc-400 bg-zinc-950/60 rounded-lg p-3 overflow-x-auto max-h-28 whitespace-pre-wrap">{c.args_preview}</pre>
            </div>
            <div class="px-4 pb-4 flex gap-2 justify-end">
              <button
                class="btn border border-zinc-700 text-zinc-400 hover:text-zinc-200 hover:border-zinc-600"
                onclick={() => confirm(c.token, false)}
                disabled={confirming === c.token}
              >
                <ShieldX class="w-3.5 h-3.5" /> Deny
              </button>
              <button
                class="btn-primary"
                onclick={() => confirm(c.token, true)}
                disabled={confirming === c.token}
              >
                <ShieldCheck class="w-3.5 h-3.5" />
                {confirming === c.token ? 'Sending…' : 'Approve'}
              </button>
            </div>
          </div>
        {/each}
      </div>
    {/if}
  </div>

  <!-- Audit log -->
  <div>
    <h2 class="text-sm font-semibold text-zinc-300 mb-3">Audit Log</h2>
    {#if audit.length === 0}
      <div class="card p-6 text-center text-zinc-600 text-sm">No audit entries</div>
    {:else}
      <div class="card divide-y divide-zinc-800">
        {#each audit as entry}
          <div class="flex items-start gap-4 px-4 py-2.5">
            <Badge variant={
              entry.event_type.includes('accepted') || entry.event_type.includes('approved') ? 'success' :
              entry.event_type.includes('rejected') || entry.event_type.includes('denied') ? 'error' : 'default'
            }>
              {entry.event_type.replace(/_/g, ' ')}
            </Badge>
            <div class="flex-1 min-w-0">
              {#if entry.details}
                <span class="text-xs text-zinc-500 font-mono">
                  {Object.entries(entry.details).filter(([,v]) => v != null).map(([k, v]) => `${k}: ${v}`).join(' · ')}
                </span>
              {/if}
            </div>
            <span class="text-xs text-zinc-600 shrink-0">{timeAgo(entry.created_at)}</span>
          </div>
        {/each}
      </div>
    {/if}
  </div>
</div>
