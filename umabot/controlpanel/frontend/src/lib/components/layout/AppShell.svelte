<script lang="ts">
  import Sidebar from './Sidebar.svelte';
  import TopBar from './TopBar.svelte';
  import Dashboard from '$lib/components/panels/Dashboard.svelte';
  import Chat from '$lib/components/panels/Chat.svelte';
  import Connectors from '$lib/components/panels/Connectors.svelte';
  import Skills from '$lib/components/panels/Skills.svelte';
  import Tasks from '$lib/components/panels/Tasks.svelte';
  import Policy from '$lib/components/panels/Policy.svelte';
  import ConfigEditor from '$lib/components/panels/ConfigEditor.svelte';
  import Logs from '$lib/components/panels/Logs.svelte';
  import { appStore } from '$lib/stores/app.svelte';

  const isChat = $derived(appStore.activePanel === 'chat');
</script>

<div class="flex h-screen overflow-hidden bg-zinc-950 text-zinc-100">
  <Sidebar />

  <div class="flex flex-col flex-1 min-w-0 overflow-hidden">
    <TopBar />
    <!-- Chat gets no padding and uses flex layout; all others get scroll + padding -->
    {#if isChat}
      <div class="flex-1 flex flex-col min-h-0">
        <Chat />
      </div>
    {:else}
      <main class="flex-1 overflow-y-auto p-6">
        {#if appStore.activePanel === 'dashboard'}
          <Dashboard />
        {:else if appStore.activePanel === 'connectors'}
          <Connectors />
        {:else if appStore.activePanel === 'skills'}
          <Skills />
        {:else if appStore.activePanel === 'tasks'}
          <Tasks />
        {:else if appStore.activePanel === 'policy'}
          <Policy />
        {:else if appStore.activePanel === 'config'}
          <ConfigEditor />
        {:else if appStore.activePanel === 'logs'}
          <Logs />
        {/if}
      </main>
    {/if}
  </div>
</div>
