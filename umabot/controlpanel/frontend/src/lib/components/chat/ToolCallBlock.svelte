<script lang="ts">
  import type { ToolCall } from '$lib/types';

  let { toolCall }: { toolCall: ToolCall } = $props();
  let expanded = $state(false);

  const argsStr = $derived(JSON.stringify(toolCall.args, null, 2));
  const resultStr = $derived(
    toolCall.result == null ? '' : typeof toolCall.result === 'string'
      ? toolCall.result
      : JSON.stringify(toolCall.result, null, 2)
  );
</script>

<div class="bg-zinc-900 border border-zinc-700/60 rounded-lg overflow-hidden text-xs">
  <button
    class="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-zinc-800/60 transition-colors"
    onclick={() => (expanded = !expanded)}
  >
    <span class="text-violet-400 font-mono shrink-0">{expanded ? '▼' : '▶'}</span>
    <code class="font-mono text-zinc-300 font-medium">{toolCall.tool_name}</code>
    <span class="text-zinc-600 ml-auto">{expanded ? 'collapse' : 'expand'}</span>
  </button>

  {#if expanded}
    <div class="border-t border-zinc-700/60 divide-y divide-zinc-700/40">
      {#if argsStr !== '{}'}
        <div class="px-3 py-2">
          <p class="text-[10px] font-semibold text-zinc-500 uppercase tracking-wider mb-1.5">Args</p>
          <pre class="font-mono text-zinc-300 overflow-x-auto text-[11px] leading-relaxed">{argsStr}</pre>
        </div>
      {/if}
      {#if resultStr}
        <div class="px-3 py-2">
          <p class="text-[10px] font-semibold text-zinc-500 uppercase tracking-wider mb-1.5">Result</p>
          <pre class="font-mono text-zinc-400 overflow-x-auto text-[11px] leading-relaxed whitespace-pre-wrap">{resultStr}</pre>
        </div>
      {/if}
    </div>
  {/if}
</div>
