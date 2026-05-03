<script lang="ts">
  import { tick } from 'svelte';
  import { chatStore } from '$lib/stores/chat.svelte';
  import { wsStore } from '$lib/ws.svelte';
  import { appStore } from '$lib/stores/app.svelte';
  import { multiAgentStore } from '$lib/stores/multiAgent.svelte';
  import { api } from '$lib/api';
  import MessageBubble from '$lib/components/chat/MessageBubble.svelte';
  import MultiAgentRunCard from '$lib/components/chat/MultiAgentRunCard.svelte';
  import ChatInput from '$lib/components/chat/ChatInput.svelte';

  let messagesEl: HTMLDivElement;
  let sending = $state(false);

  $effect(() => {
    const _ = chatStore.messages.length;
    const __ = multiAgentStore.version;
    tick().then(() => {
      if (messagesEl) messagesEl.scrollTop = messagesEl.scrollHeight;
    });
  });

  async function handleSend(text: string) {
    if (!text.trim() || sending) return;
    sending = true;
    chatStore.addMessage({ role: 'user', content: text });
    chatStore.sending = true;
    wsStore.send(text);
    sending = false;
  }

  const online = $derived(wsStore.connected && appStore.gatewayConnected);

  // Merge messages and run cards into one chronological timeline.
  const timeline = $derived((() => {
    const _ = multiAgentStore.version; // reactive dependency
    type Item =
      | { kind: 'message'; data: (typeof chatStore.messages)[0]; time: string }
      | { kind: 'run'; data: (typeof multiAgentStore.runs)[0]; time: string };
    const items: Item[] = [
      ...chatStore.messages.map(m => ({ kind: 'message' as const, data: m, time: m.created_at ?? '' })),
      ...multiAgentStore.runs.map(r => ({ kind: 'run' as const, data: r, time: r.started_at ?? '' })),
    ];
    return items.sort((a, b) => a.time.localeCompare(b.time));
  })());

  async function handleApproval(token: string, approved: boolean) {
    try {
      await api.approveAgentAction(token, approved);
      multiAgentStore.removeApproval(token);
    } catch (e) {
      console.error('Approval failed', e);
    }
  }
</script>

<!-- Fills the main area edge-to-edge (parent has p-6 removed for chat) -->
<div class="flex flex-col h-full">
  <!-- Messages -->
  <div bind:this={messagesEl} class="flex-1 overflow-y-auto px-6 py-6 min-h-0">
    {#if chatStore.loading}
      <div class="flex justify-center py-16">
        <div class="w-6 h-6 border-2 border-violet-500 border-t-transparent rounded-full animate-spin"></div>
      </div>
    {:else}
      <div class="max-w-3xl mx-auto space-y-3">
        {#if timeline.length === 0}
          <div class="flex flex-col items-center justify-center h-full gap-3 text-center py-8">
            <div class="w-16 h-16 rounded-2xl bg-zinc-800 flex items-center justify-center">
              <svg class="w-8 h-8 text-zinc-600" fill="none" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09z" />
              </svg>
            </div>
            <div>
              <p class="text-base font-medium text-zinc-300">Start a conversation</p>
              <p class="text-sm text-zinc-600 mt-1">
                {online ? 'Send a message to umaBot' : 'Gateway offline — start the gateway to chat'}
              </p>
            </div>
          </div>
        {:else}
          {#each timeline as item (item.kind === 'message' ? `msg-${item.data.id ?? item.data.content}` : `run-${item.data.run_id}`)}
            {#if item.kind === 'message'}
              <MessageBubble msg={item.data} />
            {:else}
              <MultiAgentRunCard run={item.data} />
            {/if}
          {/each}

          {#if multiAgentStore.pendingApprovals.length > 0}
            <div class="space-y-2">
              {#each multiAgentStore.pendingApprovals as approval (approval.token)}
                <div class="rounded-2xl border border-amber-500/40 bg-amber-500/10 p-4 space-y-3">
                  <div class="flex items-center gap-2">
                    <svg class="w-4 h-4 text-amber-400 shrink-0" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z" />
                    </svg>
                    <span class="text-sm font-medium text-amber-300">Approval Required</span>
                  </div>
                  <div>
                    <p class="text-xs text-zinc-400 mb-1">Reason</p>
                    <p class="text-sm text-zinc-200">{approval.reason}</p>
                  </div>
                  {#if approval.action_summary}
                    <div>
                      <p class="text-xs text-zinc-400 mb-1">Action</p>
                      <p class="text-sm text-zinc-300 rounded-lg border border-zinc-700 bg-zinc-900 p-2 font-mono text-xs">{approval.action_summary}</p>
                    </div>
                  {/if}
                  <div class="flex gap-2">
                    <button
                      class="px-4 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-sm font-medium transition-colors"
                      onclick={() => handleApproval(approval.token, true)}
                    >
                      Approve
                    </button>
                    <button
                      class="px-4 py-1.5 rounded-lg bg-zinc-700 hover:bg-zinc-600 text-zinc-200 text-sm font-medium transition-colors"
                      onclick={() => handleApproval(approval.token, false)}
                    >
                      Deny
                    </button>
                  </div>
                </div>
              {/each}
            </div>
          {/if}

          {#if chatStore.sending}
            <div class="flex items-start gap-2.5">
              <div class="w-7 h-7 rounded-full bg-violet-600 flex items-center justify-center shrink-0 mt-0.5">
                <span class="text-[11px] font-bold text-white">U</span>
              </div>
              <div class="bg-zinc-800 rounded-2xl rounded-tl-sm px-3.5 py-2.5">
                <div class="flex gap-1.5 items-center h-4">
                  <span class="w-1.5 h-1.5 rounded-full bg-zinc-400 animate-bounce" style="animation-delay: 0ms"></span>
                  <span class="w-1.5 h-1.5 rounded-full bg-zinc-400 animate-bounce" style="animation-delay: 150ms"></span>
                  <span class="w-1.5 h-1.5 rounded-full bg-zinc-400 animate-bounce" style="animation-delay: 300ms"></span>
                </div>
              </div>
            </div>
          {/if}
        {/if}
      </div>
    {/if}
  </div>

  <!-- Input — max-width constrained to match messages -->
  <div class="shrink-0 max-w-3xl mx-auto w-full">
    <ChatInput {handleSend} disabled={!online} />
  </div>
</div>
