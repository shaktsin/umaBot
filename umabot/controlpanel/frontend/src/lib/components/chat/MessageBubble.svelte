<script lang="ts">
  import type { Attachment, ChatMessage } from '$lib/types';
  import ToolCallBlock from './ToolCallBlock.svelte';

  let { msg }: { msg: ChatMessage } = $props();

  function dataUri(att: Attachment): string {
    return `data:${att.mime_type};base64,${att.data}`;
  }

  function isImage(att: Attachment): boolean {
    return att.mime_type.startsWith('image/');
  }
</script>

{#if msg.role === 'user'}
  <div class="flex justify-end">
    <div class="max-w-[85%] bg-violet-600 text-white rounded-2xl rounded-br-sm px-3.5 py-2.5 text-sm leading-relaxed">
      {msg.content}
    </div>
  </div>
{:else}
  <div class="flex flex-col gap-1">
    <div class="max-w-[90%] bg-zinc-800 text-zinc-100 rounded-2xl rounded-tl-sm px-3.5 py-2.5 text-sm leading-relaxed whitespace-pre-wrap">
      {msg.content}
    </div>

    {#if msg.attachments && msg.attachments.length > 0}
      <div class="pl-1 space-y-2 max-w-[90%]">
        {#each msg.attachments as att}
          {#if isImage(att)}
            <img
              src={dataUri(att)}
              alt={att.filename}
              class="rounded-xl max-w-full max-h-96 object-contain border border-zinc-700"
            />
          {:else}
            <a
              href={dataUri(att)}
              download={att.filename}
              class="flex items-center gap-2 bg-zinc-700 hover:bg-zinc-600 text-zinc-100 text-xs rounded-lg px-3 py-2 w-fit transition-colors"
            >
              <svg class="w-4 h-4 shrink-0" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M7 10l5 5m0 0l5-5m-5 5V3" />
              </svg>
              {att.filename}
            </a>
          {/if}
        {/each}
      </div>
    {/if}

    {#if msg.tool_calls && msg.tool_calls.length > 0}
      <div class="pl-1 space-y-1 max-w-[90%]">
        {#each msg.tool_calls as tc}
          <ToolCallBlock toolCall={tc} />
        {/each}
      </div>
    {/if}
  </div>
{/if}
