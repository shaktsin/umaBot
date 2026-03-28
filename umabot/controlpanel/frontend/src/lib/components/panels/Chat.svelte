<script lang="ts">
  import { tick } from 'svelte';
  import { chatStore } from '$lib/stores/chat.svelte';
  import { wsStore } from '$lib/ws.svelte';
  import { appStore } from '$lib/stores/app.svelte';
  import MessageBubble from '$lib/components/chat/MessageBubble.svelte';
  import ChatInput from '$lib/components/chat/ChatInput.svelte';

  let messagesEl: HTMLDivElement;
  let sending = $state(false);

  $effect(() => {
    const _ = chatStore.messages.length;
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
</script>

<!-- Fills the main area edge-to-edge (parent has p-6 removed for chat) -->
<div class="flex flex-col h-full">
  <!-- Messages -->
  <div bind:this={messagesEl} class="flex-1 overflow-y-auto px-6 py-6 min-h-0">
    {#if chatStore.loading}
      <div class="flex justify-center py-16">
        <div class="w-6 h-6 border-2 border-violet-500 border-t-transparent rounded-full animate-spin"></div>
      </div>
    {:else if chatStore.messages.length === 0}
      <div class="flex flex-col items-center justify-center h-full gap-3 text-center">
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
      <div class="max-w-3xl mx-auto space-y-3">
        {#each chatStore.messages as msg (msg.id ?? msg.content + msg.role)}
          <MessageBubble {msg} />
        {/each}
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
      </div>
    {/if}
  </div>

  <!-- Input — max-width constrained to match messages -->
  <div class="shrink-0 max-w-3xl mx-auto w-full">
    <ChatInput {handleSend} disabled={!online} />
  </div>
</div>
