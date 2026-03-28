<script lang="ts">
  import { api } from '$lib/api';
  import { RefreshCw, Download } from 'lucide-svelte';

  let lines = $state<string[]>([]);
  let logFile = $state('');
  let loading = $state(true);
  let level = $state('');
  let liveStreaming = $state(false);
  let eventSource: EventSource | null = null;
  let logsEl: HTMLDivElement;

  async function loadRecent() {
    loading = true;
    const res = await api.getRecentLogs(300, level);
    lines = res.lines;
    logFile = res.file;
    loading = false;
  }

  function toggleStream() {
    if (liveStreaming) {
      eventSource?.close();
      eventSource = null;
      liveStreaming = false;
    } else {
      const url = `/api/logs/stream${level ? `?level=${level}` : ''}`;
      eventSource = new EventSource(url);
      eventSource.onmessage = (e) => {
        const { line } = JSON.parse(e.data);
        lines = [...lines.slice(-499), line];
        requestAnimationFrame(() => {
          if (logsEl) logsEl.scrollTop = logsEl.scrollHeight;
        });
      };
      liveStreaming = true;
    }
  }

  function download() {
    const blob = new Blob([lines.join('\n')], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'umabot.log';
    a.click();
    URL.revokeObjectURL(url);
  }

  function lineColor(line: string): string {
    if (line.includes('[ERROR]') || line.includes('ERROR')) return 'text-red-400';
    if (line.includes('[WARNING]') || line.includes('WARN')) return 'text-amber-400';
    if (line.includes('[DEBUG]') || line.includes('DEBUG')) return 'text-zinc-600';
    return 'text-zinc-300';
  }

  $effect(() => {
    loadRecent();
    return () => { eventSource?.close(); };
  });
</script>

<div class="space-y-4 h-full flex flex-col">
  <div class="flex items-center justify-between shrink-0">
    <div>
      <h1 class="section-title mb-0">Logs</h1>
      {#if logFile}<p class="text-xs text-zinc-600 font-mono mt-0.5">{logFile}</p>{/if}
    </div>
    <div class="flex gap-2 items-center">
      <select class="input text-xs py-1" bind:value={level} onchange={loadRecent}>
        <option value="">All levels</option>
        <option value="DEBUG">DEBUG</option>
        <option value="INFO">INFO</option>
        <option value="WARNING">WARNING</option>
        <option value="ERROR">ERROR</option>
      </select>
      <button class="btn-ghost" onclick={loadRecent}><RefreshCw class="w-3.5 h-3.5" /></button>
      <button
        class="btn {liveStreaming ? 'bg-emerald-600/20 text-emerald-400 border border-emerald-600/30' : 'btn-ghost'}"
        onclick={toggleStream}
      >
        <span class="w-1.5 h-1.5 rounded-full {liveStreaming ? 'bg-emerald-500 animate-pulse' : 'bg-zinc-600'}"></span>
        {liveStreaming ? 'Live' : 'Stream'}
      </button>
      <button class="btn-ghost" onclick={download}><Download class="w-3.5 h-3.5" /></button>
    </div>
  </div>

  <div
    bind:this={logsEl}
    class="card flex-1 overflow-y-auto p-4 font-mono text-xs leading-relaxed min-h-0"
  >
    {#if loading}
      <div class="flex justify-center py-8">
        <div class="w-5 h-5 border-2 border-violet-500 border-t-transparent rounded-full animate-spin"></div>
      </div>
    {:else if lines.length === 0}
      <p class="text-zinc-600 text-center py-8">No log entries found</p>
    {:else}
      {#each lines as line}
        <div class="py-0.5 {lineColor(line)} hover:bg-zinc-800/40 px-1 rounded">{line}</div>
      {/each}
    {/if}
  </div>
</div>
