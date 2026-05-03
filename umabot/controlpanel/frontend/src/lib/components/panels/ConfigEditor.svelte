<script lang="ts">
  import { api } from '$lib/api';
  import { RefreshCw, Save, FileCode } from 'lucide-svelte';

  let config = $state<Record<string, unknown> | null>(null);
  let rawYaml = $state('');
  let showRaw = $state(false);
  let loading = $state(true);
  let saving = $state(false);
  let saved = $state(false);
  let error = $state('');

  // Editable fields
  let llmProvider = $state('');
  let llmModel = $state('');
  let strictness = $state('normal');
  let shellEnabled = $state(false);

  async function load() {
    loading = true;
    try {
      const [cfg, raw] = await Promise.all([api.getConfig(), api.getRawYaml()]);
      config = cfg;
      rawYaml = (raw as { yaml: string }).yaml;
      const llm = cfg.llm as Record<string, string> | undefined;
      const policy = cfg.policy as Record<string, string> | undefined;
      const tools = cfg.tools as Record<string, boolean> | undefined;
      llmProvider = llm?.provider ?? '';
      llmModel = llm?.model ?? '';
      strictness = policy?.confirmation_strictness ?? 'normal';
      shellEnabled = tools?.shell_enabled ?? false;
    } finally {
      loading = false;
    }
  }

  async function save() {
    saving = true;
    error = '';
    try {
      const existingPolicy = (config?.policy as Record<string, unknown> | undefined) ?? {};
      const existingTools = (config?.tools as Record<string, unknown> | undefined) ?? {};
      await api.updateConfig({
        llm: { provider: llmProvider, model: llmModel },
        policy: {
          ...existingPolicy,
          confirmation_strictness: strictness,
        },
        tools: {
          ...existingTools,
          shell_enabled: shellEnabled,
        },
      });
      saved = true;
      setTimeout(() => (saved = false), 2000);
    } catch (e) {
      error = (e as Error).message;
    } finally {
      saving = false;
    }
  }

  $effect(() => { load(); });
</script>

<div class="space-y-4">
  <div class="flex items-center justify-between">
    <h1 class="section-title mb-0">Configuration</h1>
    <div class="flex gap-2">
      <button class="btn-ghost" onclick={() => (showRaw = !showRaw)}>
        <FileCode class="w-3.5 h-3.5" /> {showRaw ? 'Form' : 'Raw YAML'}
      </button>
      <button class="btn-ghost" onclick={load}><RefreshCw class="w-3.5 h-3.5" /></button>
      <button class="btn-primary" onclick={save} disabled={saving}>
        <Save class="w-3.5 h-3.5" /> {saving ? 'Saving…' : saved ? 'Saved!' : 'Save & Reload'}
      </button>
    </div>
  </div>

  {#if error}
    <div class="card p-3 border-red-500/20 bg-red-500/5 text-red-400 text-sm">{error}</div>
  {/if}

  {#if loading}
    <div class="space-y-3">{#each Array(3) as _}<div class="card h-28 animate-pulse"></div>{/each}</div>
  {:else if showRaw}
    <div class="card p-4">
      <pre class="text-xs font-mono text-zinc-300 overflow-auto max-h-[70vh] leading-relaxed">{rawYaml}</pre>
    </div>
  {:else}
    <!-- LLM Settings -->
    <div class="card p-5 space-y-4">
      <h2 class="text-sm font-semibold text-zinc-300 border-b border-zinc-800 pb-3">LLM Provider</h2>
      <div class="grid grid-cols-2 gap-4">
        <div>
          <label class="label block mb-1.5" for="llm-provider">Provider</label>
          <select id="llm-provider" class="input w-full" bind:value={llmProvider}>
            <option value="openai">OpenAI</option>
            <option value="claude">Claude (Anthropic)</option>
            <option value="gemini">Gemini</option>
          </select>
        </div>
        <div>
          <label class="label block mb-1.5" for="llm-model">Model</label>
          <input id="llm-model" class="input w-full" placeholder="gpt-4o-mini" bind:value={llmModel} />
        </div>
      </div>
    </div>

    <!-- Policy settings -->
    <div class="card p-5 space-y-4">
      <h2 class="text-sm font-semibold text-zinc-300 border-b border-zinc-800 pb-3">Policy</h2>
      <div class="flex items-center justify-between">
        <div>
          <p class="text-sm text-zinc-200">Confirmation Strictness</p>
          <p class="text-xs text-zinc-500 mt-0.5">How aggressively to require confirmation for tool calls</p>
        </div>
        <select class="input" bind:value={strictness}>
          <option value="normal">Normal</option>
          <option value="strict">Strict</option>
        </select>
      </div>
      <div class="flex items-center justify-between">
        <div>
          <p class="text-sm text-zinc-200">Shell Tools</p>
          <p class="text-xs text-zinc-500 mt-0.5">Allow the bot to run shell commands</p>
        </div>
        <button
          class="relative w-10 h-5 rounded-full transition-colors {shellEnabled ? 'bg-violet-600' : 'bg-zinc-700'}"
          onclick={() => (shellEnabled = !shellEnabled)}
          role="switch"
          aria-label="Toggle shell tools"
          aria-checked={shellEnabled}
        >
          <span class="absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform {shellEnabled ? 'translate-x-5' : 'translate-x-0'}"></span>
        </button>
      </div>
    </div>
  {/if}
</div>
