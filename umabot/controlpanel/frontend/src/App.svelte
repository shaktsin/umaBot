<script lang="ts">
  import { onMount } from 'svelte';
  import AppShell from '$lib/components/layout/AppShell.svelte';
  import { wsStore } from '$lib/ws.svelte';
  import { chatStore } from '$lib/stores/chat.svelte';

  onMount(() => {
    wsStore.connect();
    chatStore.loadHistory();
    return () => wsStore.disconnect();
  });
</script>

<AppShell />
