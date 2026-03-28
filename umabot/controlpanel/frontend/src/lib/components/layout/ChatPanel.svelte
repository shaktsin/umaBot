<script lang="ts">
  import { tick } from 'svelte';
  import { chatStore } from '$lib/stores/chat.svelte';
  import { wsStore } from '$lib/ws.svelte';
  import { appStore } from '$lib/stores/app.svelte';
  import MessageBubble from '$lib/components/chat/MessageBubble.svelte';
  import ChatInput from '$lib/components/chat/ChatInput.svelte';
  import { X } from 'lucide-svelte';

  let messagesEl: HTMLDivElement;
  let sending = $state(false);

  $effect(() => {
    // Auto-scroll on new messages
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
    // The assistant response comes via WS event and is handled in wsStore
    sending = false;
  }
</script>

<aside class="flex flex-col w-80 xl:w-96 shrink-0 border-l border-zinc-800 bg-zinc-900">
  <!-- Header -->
  <div class="flex items-center justify-between px-4 h-12 border-b border-zinc-800 shrink-0">
    <div class="flex items-center gap-2">
      <div class="w-2 h-2 rounded-full {wsStore.connected && appStore.gatewayConnected ? 'bg-emerald-500' : 'bg-zinc-600'}"></div>
      <span class="text-sm font-medium text-zinc-300">Chat</span>
    </div>
    <button
      class="p-1.5 text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800 rounded-lg transition-colors"
      onclick={() => (appStore.chatCollapsed = true)}
    >
      <X class="w-3.5 h-3.5" />
    </button>
  </div>

  <!-- Messages -->
  <div bind:this={messagesEl} class="flex-1 overflow-y-auto px-3 py-4 space-y-2">
    {#if chatStore.loading}
      <div class="flex justify-center py-8">
        <div class="w-5 h-5 border-2 border-violet-500 border-t-transparent rounded-full animate-spin"></div>
      </div>
    {:else if chatStore.messages.length === 0}
      <div class="flex flex-col items-center justify-center h-full text-center py-12">
        <div class="w-12 h-12 rounded-full bg-zinc-800 flex items-center justify-center mb-3">
          <svg class="w-6 h-6 text-zinc-500" fill="none" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09z" />
          </svg>
        </div>
        <p class="text-sm text-zinc-400">Start a conversation</p>
        <p class="text-xs text-zinc-600 mt-1">Send a message to umaBot</p>
      </div>
    {:else}
      {#each chatStore.messages as msg (msg.id ?? msg.content + msg.role)}
        <MessageBubble {msg} />
      {/each}
      {#if chatStore.sending}
        <div class="flex items-start gap-2">
          <div class="w-6 h-6 rounded-full bg-violet-600 flex items-center justify-center shrink-0 mt-0.5">
            <span class="text-[10px] font-bold">U</span>
          </div>
          <div class="bg-zinc-800 rounded-2xl rounded-tl-sm px-3 py-2">
            <div class="flex gap-1 items-center h-4">
              <span class="w-1.5 h-1.5 rounded-full bg-zinc-400 animate-bounce" style="animation-delay: 0ms"></span>
              <span class="w-1.5 h-1.5 rounded-full bg-zinc-400 animate-bounce" style="animation-delay: 150ms"></span>
              <span class="w-1.5 h-1.5 rounded-full bg-zinc-400 animate-bounce" style="animation-delay: 300ms"></span>
            </div>
          </div>
        </div>
      {/if}
    {/if}
  </div>

  <!-- Input -->
  <ChatInput {handleSend} disabled={!wsStore.connected || !appStore.gatewayConnected} />
</aside>
