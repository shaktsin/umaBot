<script lang="ts">
  let { handleSend, disabled = false }: { handleSend: (text: string) => void; disabled?: boolean } = $props();
  let text = $state('');

  function submit() {
    const t = text.trim();
    if (!t || disabled) return;
    handleSend(t);
    text = '';
  }

  function onKeydown(e: KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }
</script>

<div class="px-3 pb-3 pt-2 border-t border-zinc-800 shrink-0">
  <div class="flex items-end gap-2">
    <textarea
      bind:value={text}
      onkeydown={onKeydown}
      placeholder={disabled ? 'Gateway offline…' : 'Message umaBot…'}
      {disabled}
      rows="1"
      class="flex-1 bg-zinc-800 border border-zinc-700 text-zinc-100 rounded-xl px-3 py-2 text-sm
             placeholder:text-zinc-500 focus:outline-none focus:ring-1 focus:ring-violet-500 focus:border-violet-500
             resize-none transition-colors min-h-[36px] max-h-[120px] disabled:opacity-40 disabled:cursor-not-allowed"
      style="field-sizing: content"
    ></textarea>
    <button
      onclick={submit}
      {disabled}
      aria-label="Send message"
      class="p-2 bg-violet-600 hover:bg-violet-500 disabled:opacity-40 disabled:cursor-not-allowed
             text-white rounded-xl transition-colors shrink-0"
    >
      <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" d="M6 12L3.269 3.126A59.768 59.768 0 0121.485 12 59.77 59.77 0 013.27 20.876L5.999 12zm0 0h7.5" />
      </svg>
    </button>
  </div>
  <p class="text-[10px] text-zinc-600 mt-1.5 text-right">Enter to send · Shift+Enter for newline</p>
</div>
