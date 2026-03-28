<script lang="ts">
  import { api, timeAgo } from '$lib/api';
  import type { Task } from '$lib/types';
  import Badge from '$lib/components/ui/Badge.svelte';
  import { RefreshCw, Plus, X } from 'lucide-svelte';

  let tasks = $state<Task[]>([]);
  let loading = $state(true);
  let showForm = $state(false);
  let form = $state({ name: '', prompt: '', task_type: 'one_time', next_run_at: '', timezone: 'UTC' });
  let submitting = $state(false);
  let statusFilter = $state('');

  async function load() {
    tasks = await api.getTasks(statusFilter || undefined);
    loading = false;
  }

  async function create() {
    if (!form.name || !form.prompt) return;
    submitting = true;
    try {
      await api.createTask({ ...form, schedule: {} });
      showForm = false;
      form = { name: '', prompt: '', task_type: 'one_time', next_run_at: '', timezone: 'UTC' };
      await load();
    } finally {
      submitting = false;
    }
  }

  async function cancel(id: number) {
    if (!confirm('Cancel this task?')) return;
    await api.cancelTask(id);
    await load();
  }

  $effect(() => { load(); });

  const statusVariant = (s: string) =>
    s === 'active' ? 'success' : s === 'cancelled' ? 'error' : 'default';
</script>

<div class="space-y-4">
  <div class="flex items-center justify-between">
    <h1 class="section-title mb-0">Tasks</h1>
    <div class="flex gap-2">
      <select class="input text-xs py-1" bind:value={statusFilter} onchange={load}>
        <option value="">All</option>
        <option value="active">Active</option>
        <option value="completed">Completed</option>
        <option value="cancelled">Cancelled</option>
      </select>
      <button class="btn-ghost" onclick={load}><RefreshCw class="w-3.5 h-3.5" /></button>
      <button class="btn-primary" onclick={() => (showForm = !showForm)}><Plus class="w-3.5 h-3.5" /> New</button>
    </div>
  </div>

  {#if showForm}
    <div class="card p-4 space-y-3">
      <h2 class="text-sm font-semibold text-zinc-300">New Task</h2>
      <div class="grid grid-cols-2 gap-3">
        <div>
          <label class="label block mb-1" for="task-name">Name</label>
          <input id="task-name" class="input w-full" placeholder="Daily summary" bind:value={form.name} />
        </div>
        <div>
          <label class="label block mb-1" for="task-type">Type</label>
          <select id="task-type" class="input w-full" bind:value={form.task_type}>
            <option value="one_time">One-time</option>
            <option value="periodic">Periodic</option>
          </select>
        </div>
      </div>
      <div>
        <label class="label block mb-1" for="task-prompt">Prompt</label>
        <textarea id="task-prompt" class="input w-full" rows="3" placeholder="Summarize today's messages and send a report" bind:value={form.prompt}></textarea>
      </div>
      {#if form.task_type === 'one_time'}
        <div>
          <label class="label block mb-1" for="task-run-at">Run At (ISO datetime)</label>
          <input id="task-run-at" class="input w-full" type="datetime-local" bind:value={form.next_run_at} />
        </div>
      {/if}
      <div class="flex gap-2 justify-end">
        <button class="btn-ghost" onclick={() => (showForm = false)}>Cancel</button>
        <button class="btn-primary" onclick={create} disabled={submitting}>{submitting ? 'Creating…' : 'Create'}</button>
      </div>
    </div>
  {/if}

  {#if loading}
    <div class="space-y-2">{#each Array(3) as _}<div class="card h-16 animate-pulse"></div>{/each}</div>
  {:else if tasks.length === 0}
    <div class="card p-8 text-center text-zinc-500 text-sm">No tasks</div>
  {:else}
    <div class="space-y-2">
      {#each tasks as task}
        <div class="card p-4">
          <div class="flex items-start justify-between gap-4">
            <div class="min-w-0 flex-1">
              <div class="flex items-center gap-2">
                <p class="text-sm font-semibold text-zinc-100">{task.name}</p>
                <Badge variant={statusVariant(task.status)}>{task.status}</Badge>
                <Badge variant="default">{task.task_type}</Badge>
              </div>
              <p class="text-xs text-zinc-500 mt-1 line-clamp-2">{task.prompt}</p>
              {#if task.next_run_at}
                <p class="text-xs text-zinc-600 mt-1">Next: {new Date(task.next_run_at).toLocaleString()}</p>
              {/if}
              {#if task.last_run_at}
                <p class="text-xs text-zinc-600">Last run: {timeAgo(task.last_run_at)}</p>
              {/if}
            </div>
            {#if task.status === 'active'}
              <button class="btn-danger shrink-0" onclick={() => cancel(task.id)}><X class="w-3.5 h-3.5" /> Cancel</button>
            {/if}
          </div>
        </div>
      {/each}
    </div>
  {/if}
</div>
