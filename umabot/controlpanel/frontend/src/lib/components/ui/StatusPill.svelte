<script lang="ts">
  let { status }: { status: string } = $props();

  const cfg: Record<string, { color: string; dot: string; label: string }> = {
    connected: { color: 'text-emerald-400', dot: 'bg-emerald-500', label: 'connected' },
    disconnected: { color: 'text-zinc-500', dot: 'bg-zinc-600', label: 'offline' },
    connecting: { color: 'text-amber-400', dot: 'bg-amber-500', label: 'connecting' },
    error: { color: 'text-red-400', dot: 'bg-red-500', label: 'error' },
    unknown: { color: 'text-zinc-500', dot: 'bg-zinc-700', label: 'unknown' },
  };

  const c = $derived(cfg[status] ?? cfg.unknown);
</script>

<span class="inline-flex items-center gap-1.5 {c.color}">
  <span
    class="h-1.5 w-1.5 rounded-full {c.dot} {status === 'connecting' ? 'animate-pulse' : ''}"
  ></span>
  <span class="text-xs">{c.label}</span>
</span>
