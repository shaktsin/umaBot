<script lang="ts">
  let {
    label,
    value,
    sub = '',
    accent = false,
    warn = false,
  }: {
    label: string;
    value: string | number;
    sub?: string;
    accent?: boolean;
    warn?: boolean;
  } = $props();
</script>

<div class="card p-5 flex flex-col gap-1">
  <div class="text-[11px] font-semibold text-zinc-500 uppercase tracking-wider">{label}</div>
  <div
    class="text-3xl font-semibold tabular-nums
           {accent ? 'text-violet-400' : warn && Number(value) > 0 ? 'text-amber-400' : 'text-zinc-100'}"
  >
    {value}
  </div>
  {#if sub}
    <div class="text-xs text-zinc-500 mt-0.5">{sub}</div>
  {/if}
</div>
