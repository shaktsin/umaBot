<script lang="ts">
  import { api } from '$lib/api';
  import type { LLMProviderInfo } from '$lib/types';
  import { RefreshCw, Save, CheckCircle2, Play, Bot } from 'lucide-svelte';

  type EditableProvider = LLMProviderInfo & {
    modelsText: string;
    activeModelInput: string;
    apiKeyInput: string;
    busy: boolean;
    error: string;
  };

  let providers = $state<EditableProvider[]>([]);
  let agentsEnabled = $state(false);
  let loading = $state(true);
  let agentsSaving = $state(false);
  let error = $state('');
  let notice = $state('');

  async function load() {
    loading = true;
    error = '';
    try {
      const data = await api.getLlmProviders();
      agentsEnabled = data.agents_enabled;
      providers = data.providers.map((provider) => ({
        ...provider,
        modelsText: provider.models.join(', '),
        activeModelInput: provider.active ? data.active_model : provider.default_model || provider.models[0] || '',
        apiKeyInput: '',
        busy: false,
        error: '',
      }));
    } catch (e) {
      error = (e as Error).message;
    } finally {
      loading = false;
    }
  }

  function updateProvider(name: string, patch: Partial<EditableProvider>) {
    providers = providers.map((provider) =>
      provider.name === name ? { ...provider, ...patch } : provider,
    );
  }

  function parseModels(text: string): string[] {
    return text
      .split(',')
      .map((item) => item.trim())
      .filter((item, idx, arr) => item.length > 0 && arr.indexOf(item) === idx);
  }

  async function saveProvider(name: string) {
    const provider = providers.find((item) => item.name === name);
    if (!provider) return;
    updateProvider(name, { busy: true, error: '' });
    notice = '';
    try {
      const payload: {
        enabled: boolean;
        models: string[];
        default_model: string;
        api_key?: string;
      } = {
        enabled: provider.enabled,
        models: parseModels(provider.modelsText),
        default_model: provider.default_model.trim(),
      };
      if (provider.apiKeyInput.trim()) {
        payload.api_key = provider.apiKeyInput.trim();
      }
      await api.updateLlmProvider(name, payload);
      notice = `${provider.label} settings saved`;
      await load();
    } catch (e) {
      updateProvider(name, { error: (e as Error).message });
    } finally {
      updateProvider(name, { busy: false });
    }
  }

  async function setActive(name: string) {
    const provider = providers.find((item) => item.name === name);
    if (!provider) return;
    const activeModel = provider.activeModelInput.trim() || provider.default_model.trim();
    updateProvider(name, { busy: true, error: '' });
    notice = '';
    try {
      await api.updateLlmProvider(name, {
        set_active: true,
        active_model: activeModel,
      });
      notice = `${provider.label} is now the active provider`;
      await load();
    } catch (e) {
      updateProvider(name, { error: (e as Error).message });
    } finally {
      updateProvider(name, { busy: false });
    }
  }

  async function toggleEnabled(name: string) {
    const provider = providers.find((item) => item.name === name);
    if (!provider) return;
    updateProvider(name, { busy: true, error: '' });
    notice = '';
    try {
      await api.updateLlmProvider(name, { enabled: !provider.enabled });
      notice = `${provider.label} ${provider.enabled ? 'disabled' : 'enabled'}`;
      await load();
    } catch (e) {
      updateProvider(name, { error: (e as Error).message });
    } finally {
      updateProvider(name, { busy: false });
    }
  }

  async function toggleAgents() {
    agentsSaving = true;
    error = '';
    notice = '';
    try {
      await api.updateAgents({ enabled: !agentsEnabled });
      agentsEnabled = !agentsEnabled;
      notice = `Multi-agent ${agentsEnabled ? 'enabled' : 'disabled'}`;
    } catch (e) {
      error = (e as Error).message;
    } finally {
      agentsSaving = false;
    }
  }

  $effect(() => { load(); });
</script>

<div class="space-y-4">
  <div class="flex items-center justify-between">
    <h1 class="section-title mb-0">LLM Providers</h1>
    <button class="btn-ghost" onclick={load}><RefreshCw class="w-3.5 h-3.5" /> Refresh</button>
  </div>

  {#if error}
    <div class="card p-3 border-red-500/20 bg-red-500/5 text-red-400 text-sm">{error}</div>
  {/if}
  {#if notice}
    <div class="card p-3 border-emerald-500/20 bg-emerald-500/5 text-emerald-400 text-sm">{notice}</div>
  {/if}

  <div class="card p-5 space-y-4">
    <h2 class="text-sm font-semibold text-zinc-300 border-b border-zinc-800 pb-3">Agent Runtime</h2>
    <div class="flex items-center justify-between">
      <div class="flex items-start gap-3">
        <Bot class="w-4 h-4 text-zinc-400 mt-0.5" />
        <div>
          <p class="text-sm text-zinc-200">Multi-agent orchestration</p>
          <p class="text-xs text-zinc-500 mt-0.5">
            Route requests through orchestrator + worker agents.
          </p>
        </div>
      </div>
      <button
        class="relative w-10 h-5 rounded-full transition-colors {agentsEnabled ? 'bg-violet-600' : 'bg-zinc-700'}"
        onclick={toggleAgents}
        disabled={agentsSaving}
        role="switch"
        aria-label="Toggle multi-agent mode"
        aria-checked={agentsEnabled}
      >
        <span class="absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform {agentsEnabled ? 'translate-x-5' : 'translate-x-0'}"></span>
      </button>
    </div>
  </div>

  {#if loading}
    <div class="space-y-3">{#each Array(2) as _}<div class="card h-52 animate-pulse"></div>{/each}</div>
  {:else}
    <div class="space-y-3">
      {#each providers as provider}
        <div class="card p-5 space-y-4">
          <div class="flex items-start justify-between gap-4">
            <div>
              <p class="text-base font-semibold text-zinc-100">{provider.label}</p>
              <p class="text-xs text-zinc-500 mt-1">{provider.name}</p>
            </div>
            <div class="flex items-center gap-2">
              {#if provider.active}
                <span class="inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs bg-emerald-500/15 text-emerald-400">
                  <CheckCircle2 class="w-3.5 h-3.5" /> Active
                </span>
              {/if}
              <button
                class="relative w-10 h-5 rounded-full transition-colors {provider.enabled ? 'bg-violet-600' : 'bg-zinc-700'}"
                onclick={() => toggleEnabled(provider.name)}
                disabled={provider.busy}
                role="switch"
                aria-label={`Toggle ${provider.label}`}
                aria-checked={provider.enabled}
              >
                <span class="absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform {provider.enabled ? 'translate-x-5' : 'translate-x-0'}"></span>
              </button>
            </div>
          </div>

          <div class="grid grid-cols-1 lg:grid-cols-2 gap-3">
            <div>
              <label class="label block mb-1.5" for={`models-${provider.name}`}>Supported Models</label>
              <input
                id={`models-${provider.name}`}
                class="input w-full"
                value={provider.modelsText}
                oninput={(event) => updateProvider(provider.name, { modelsText: (event.currentTarget as HTMLInputElement).value })}
              />
              <p class="text-[11px] text-zinc-600 mt-1">Comma-separated model IDs.</p>
            </div>

            <div>
              <label class="label block mb-1.5" for={`default-${provider.name}`}>Default Model</label>
              <input
                id={`default-${provider.name}`}
                class="input w-full"
                value={provider.default_model}
                oninput={(event) => updateProvider(provider.name, { default_model: (event.currentTarget as HTMLInputElement).value })}
              />
            </div>

            <div>
              <label class="label block mb-1.5" for={`api-key-${provider.name}`}>API Key</label>
              <input
                id={`api-key-${provider.name}`}
                class="input w-full"
                type="password"
                placeholder={provider.api_key_configured ? 'Configured (enter new to rotate)' : 'sk-...'}
                value={provider.apiKeyInput}
                oninput={(event) => updateProvider(provider.name, { apiKeyInput: (event.currentTarget as HTMLInputElement).value })}
              />
            </div>

            <div>
              <label class="label block mb-1.5" for={`active-model-${provider.name}`}>Active Model (when selected)</label>
              <input
                id={`active-model-${provider.name}`}
                class="input w-full"
                value={provider.activeModelInput}
                oninput={(event) => updateProvider(provider.name, { activeModelInput: (event.currentTarget as HTMLInputElement).value })}
              />
            </div>
          </div>

          {#if provider.error}
            <div class="text-xs text-red-400">{provider.error}</div>
          {/if}

          <div class="flex items-center justify-end gap-2">
            <button class="btn-ghost" onclick={() => setActive(provider.name)} disabled={provider.busy || !provider.enabled}>
              <Play class="w-3.5 h-3.5" /> Set Active
            </button>
            <button class="btn-primary" onclick={() => saveProvider(provider.name)} disabled={provider.busy}>
              <Save class="w-3.5 h-3.5" /> {provider.busy ? 'Saving…' : 'Save Provider'}
            </button>
          </div>
        </div>
      {/each}
    </div>
  {/if}
</div>
