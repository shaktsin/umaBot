<script lang="ts">
  import type { Attachment, ChatMessage } from '$lib/types';
  import { api } from '$lib/api';
  import ToolCallBlock from './ToolCallBlock.svelte';

  let { msg }: { msg: ChatMessage } = $props();
  let expanded = $state<Record<string, boolean>>({});
  let textPreviewCache = $state<Record<string, string>>({});
  let linkedAttachmentCache = $state<Record<string, Attachment>>({});
  let linkedLoading = $state<Record<string, boolean>>({});
  let linkedError = $state<Record<string, string>>({});

  const MAX_PREVIEW_CHARS = 12000;
  const SANDBOX_MD_LINK_RE = /\[([^\]]+)\]\((sandbox:[^)]+)\)/gi;

  interface SandboxLink {
    filename: string;
    path: string;
  }

  function dataUri(att: Attachment): string {
    return `data:${att.mime_type};base64,${att.data}`;
  }

  function isImage(att: Attachment): boolean {
    return att.mime_type.startsWith('image/');
  }

  function isPdf(att: Attachment): boolean {
    return att.mime_type === 'application/pdf';
  }

  function isTextLike(att: Attachment): boolean {
    return (
      att.mime_type.startsWith('text/') ||
      att.mime_type === 'application/json' ||
      att.mime_type === 'application/xml'
    );
  }

  function keyFor(att: Attachment, index: number): string {
    return `${att.filename}#${index}`;
  }

  function keyForLink(link: SandboxLink, index: number): string {
    return `sandbox:${link.path}#${index}`;
  }

  function toggleInline(att: Attachment, index: number): void {
    const key = keyFor(att, index);
    const next = !expanded[key];
    expanded[key] = next;
    if (next && isTextLike(att) && !textPreviewCache[key]) {
      textPreviewCache[key] = decodeBase64Text(att.data);
    }
  }

  async function toggleSandbox(link: SandboxLink, index: number): Promise<void> {
    const key = keyForLink(link, index);
    const next = !expanded[key];
    expanded[key] = next;
    if (!next) return;
    if (!linkedAttachmentCache[key] && !linkedLoading[key]) {
      linkedLoading[key] = true;
      linkedError[key] = '';
      try {
        linkedAttachmentCache[key] = await api.getChatAttachment(link.path);
      } catch (err) {
        linkedError[key] = err instanceof Error ? err.message : 'Failed to load attachment';
      } finally {
        linkedLoading[key] = false;
      }
    }
    const att = linkedAttachmentCache[key];
    if (att && isTextLike(att) && !textPreviewCache[key]) {
      textPreviewCache[key] = decodeBase64Text(att.data);
    }
  }

  function extractSandboxLinks(content: string): SandboxLink[] {
    const out: SandboxLink[] = [];
    const seen = new Set<string>();
    for (const match of content.matchAll(SANDBOX_MD_LINK_RE)) {
      const filename = (match[1] ?? '').trim();
      const path = (match[2] ?? '').trim();
      if (!filename || !path || seen.has(path)) continue;
      seen.add(path);
      out.push({ filename, path });
    }
    return out;
  }

  function stripSandboxLinks(content: string): string {
    return content.replaceAll(SANDBOX_MD_LINK_RE, '$1');
  }

  let parsedSandboxLinks = $derived.by(() => {
    const all = extractSandboxLinks(msg.content ?? '');
    const fromAttachments = new Set((msg.attachments ?? []).map((a) => a.filename.toLowerCase()));
    return all.filter((link) => !fromAttachments.has(link.filename.toLowerCase()));
  });
  let contentForRender = $derived(stripSandboxLinks(msg.content ?? ''));

  function decodeBase64Text(data: string): string {
    try {
      const bin = atob(data);
      const bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0));
      const text = new TextDecoder().decode(bytes);
      if (text.length > MAX_PREVIEW_CHARS) {
        return `${text.slice(0, MAX_PREVIEW_CHARS)}\n\n... [truncated]`;
      }
      return text;
    } catch {
      return 'Preview unavailable.';
    }
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
      {contentForRender}
    </div>

    {#if msg.attachments && msg.attachments.length > 0}
      <div class="pl-1 space-y-2 max-w-[90%]">
        {#each msg.attachments as att, i}
          {@const k = keyFor(att, i)}
          <div class="rounded-xl border border-zinc-700 bg-zinc-800/70 overflow-hidden">
            <button
              class="w-full flex items-center justify-between gap-3 px-3 py-2 text-left hover:bg-zinc-700/60 transition-colors"
              onclick={() => toggleInline(att, i)}
            >
              <div class="flex min-w-0 items-center gap-2">
                <svg class="w-4 h-4 shrink-0 text-zinc-300" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" d="M19.5 14.25v4.125A2.625 2.625 0 0116.875 21H7.125A2.625 2.625 0 014.5 18.375V14.25M7.5 10.5l4.5 4.5m0 0l4.5-4.5m-4.5 4.5V3" />
                </svg>
                <div class="min-w-0">
                  <div class="text-xs text-zinc-100 truncate">{att.filename}</div>
                  <div class="text-[11px] text-zinc-400">{att.mime_type}</div>
                </div>
              </div>
              <svg
                class="w-4 h-4 shrink-0 text-zinc-400 transition-transform {expanded[k] ? 'rotate-180' : ''}"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
                viewBox="0 0 24 24"
              >
                <path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7" />
              </svg>
            </button>

            {#if expanded[k]}
              <div class="border-t border-zinc-700 p-2.5 space-y-2">
                {#if isImage(att)}
                  <img
                    src={dataUri(att)}
                    alt={att.filename}
                    class="rounded-lg w-full max-h-96 object-contain border border-zinc-700 bg-zinc-900"
                  />
                {:else if isPdf(att)}
                  <iframe
                    title={att.filename}
                    src={dataUri(att)}
                    class="w-full h-80 rounded-lg border border-zinc-700 bg-zinc-900"
                  ></iframe>
                {:else if isTextLike(att)}
                  <pre class="max-h-80 overflow-auto rounded-lg border border-zinc-700 bg-zinc-900 p-3 text-[11px] text-zinc-200 whitespace-pre-wrap break-words">{textPreviewCache[k] ?? ''}</pre>
                {:else}
                  <div class="text-xs text-zinc-400">Preview not available for this file type.</div>
                {/if}

                <a
                  href={dataUri(att)}
                  download={att.filename}
                  class="inline-flex items-center gap-2 bg-zinc-700 hover:bg-zinc-600 text-zinc-100 text-xs rounded-lg px-3 py-2 transition-colors"
                >
                  <svg class="w-4 h-4 shrink-0" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M7 10l5 5m0 0l5-5m-5 5V3" />
                  </svg>
                  Download
                </a>
              </div>
            {/if}
          </div>
        {/each}
      </div>
    {/if}

    {#if parsedSandboxLinks.length > 0}
      <div class="pl-1 space-y-2 max-w-[90%]">
        {#each parsedSandboxLinks as link, i}
          {@const k = keyForLink(link, i)}
          {@const loaded = linkedAttachmentCache[k]}
          <div class="rounded-xl border border-zinc-700 bg-zinc-800/70 overflow-hidden">
            <button
              class="w-full flex items-center justify-between gap-3 px-3 py-2 text-left hover:bg-zinc-700/60 transition-colors"
              onclick={() => toggleSandbox(link, i)}
            >
              <div class="flex min-w-0 items-center gap-2">
                <svg class="w-4 h-4 shrink-0 text-zinc-300" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" d="M19.5 14.25v4.125A2.625 2.625 0 0116.875 21H7.125A2.625 2.625 0 014.5 18.375V14.25M7.5 10.5l4.5 4.5m0 0l4.5-4.5m-4.5 4.5V3" />
                </svg>
                <div class="min-w-0">
                  <div class="text-xs text-zinc-100 truncate">{link.filename}</div>
                  <div class="text-[11px] text-zinc-400">{loaded?.mime_type ?? 'sandbox file'}</div>
                </div>
              </div>
              <svg
                class="w-4 h-4 shrink-0 text-zinc-400 transition-transform {expanded[k] ? 'rotate-180' : ''}"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
                viewBox="0 0 24 24"
              >
                <path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7" />
              </svg>
            </button>

            {#if expanded[k]}
              <div class="border-t border-zinc-700 p-2.5 space-y-2">
                {#if linkedLoading[k]}
                  <div class="text-xs text-zinc-400">Loading preview...</div>
                {:else if linkedError[k]}
                  <div class="text-xs text-red-300">{linkedError[k]}</div>
                {:else if loaded}
                  {#if isImage(loaded)}
                    <img
                      src={dataUri(loaded)}
                      alt={loaded.filename}
                      class="rounded-lg w-full max-h-96 object-contain border border-zinc-700 bg-zinc-900"
                    />
                  {:else if isPdf(loaded)}
                    <iframe
                      title={loaded.filename}
                      src={dataUri(loaded)}
                      class="w-full h-80 rounded-lg border border-zinc-700 bg-zinc-900"
                    ></iframe>
                  {:else if isTextLike(loaded)}
                    <pre class="max-h-80 overflow-auto rounded-lg border border-zinc-700 bg-zinc-900 p-3 text-[11px] text-zinc-200 whitespace-pre-wrap break-words">{textPreviewCache[k] ?? ''}</pre>
                  {:else}
                    <div class="text-xs text-zinc-400">Preview not available for this file type.</div>
                  {/if}
                  <a
                    href={dataUri(loaded)}
                    download={loaded.filename}
                    class="inline-flex items-center gap-2 bg-zinc-700 hover:bg-zinc-600 text-zinc-100 text-xs rounded-lg px-3 py-2 transition-colors"
                  >
                    <svg class="w-4 h-4 shrink-0" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M7 10l5 5m0 0l5-5m-5 5V3" />
                    </svg>
                    Download
                  </a>
                {/if}
              </div>
            {/if}
          </div>
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
