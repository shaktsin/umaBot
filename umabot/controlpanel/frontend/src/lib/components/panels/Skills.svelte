<script lang="ts">
  import { api } from '$lib/api';
  import type { Skill } from '$lib/types';
  import Badge from '$lib/components/ui/Badge.svelte';
  import { RefreshCw, Plus, Trash2 } from 'lucide-svelte';

  let skills = $state<Skill[]>([]);
  let loading = $state(true);
  let installing = $state(false);
  let installSource = $state('');
  let installName = $state('');
  let showInstallForm = $state(false);
  let error = $state('');

  async function load() {
    skills = await api.getSkills();
    loading = false;
  }

  async function install() {
    if (!installSource.trim()) return;
    installing = true;
    error = '';
    try {
      await api.installSkill(installSource.trim(), installName.trim() || undefined);
      installSource = '';
      installName = '';
      showInstallForm = false;
      await load();
    } catch (e) {
      error = (e as Error).message;
    } finally {
      installing = false;
    }
  }

  async function remove(name: string) {
    if (!confirm(`Remove skill "${name}"?`)) return;
    await api.removeSkill(name);
    await load();
  }

  $effect(() => { load(); });
</script>

<div class="space-y-4">
  <div class="flex items-center justify-between">
    <h1 class="section-title mb-0">Skills</h1>
    <div class="flex gap-2">
      <button class="btn-ghost" onclick={load}><RefreshCw class="w-3.5 h-3.5" /></button>
      <button class="btn-primary" onclick={() => (showInstallForm = !showInstallForm)}>
        <Plus class="w-3.5 h-3.5" /> Install
      </button>
    </div>
  </div>

  {#if showInstallForm}
    <div class="card p-4 space-y-3">
      <h2 class="text-sm font-semibold text-zinc-300">Install Skill</h2>
      <input class="input w-full" placeholder="Git URL or local path" bind:value={installSource} />
      <input class="input w-full" placeholder="Custom name (optional)" bind:value={installName} />
      {#if error}
        <p class="text-xs text-red-400">{error}</p>
      {/if}
      <div class="flex gap-2 justify-end">
        <button class="btn-ghost" onclick={() => (showInstallForm = false)}>Cancel</button>
        <button class="btn-primary" onclick={install} disabled={installing}>
          {installing ? 'Installing…' : 'Install'}
        </button>
      </div>
    </div>
  {/if}

  {#if loading}
    <div class="space-y-2">{#each Array(3) as _}<div class="card h-16 animate-pulse"></div>{/each}</div>
  {:else if skills.length === 0}
    <div class="card p-8 text-center text-zinc-500 text-sm">No skills installed</div>
  {:else}
    <div class="space-y-2">
      {#each skills as skill}
        <div class="card p-4 flex items-start justify-between gap-4">
          <div class="min-w-0">
            <div class="flex items-center gap-2">
              <p class="text-sm font-semibold text-zinc-100">{skill.name}</p>
              {#if skill.license}
                <Badge variant="default">{skill.license}</Badge>
              {/if}
            </div>
            <p class="text-xs text-zinc-500 mt-0.5 truncate">{skill.description || 'No description'}</p>
            {#if skill.source_dir}
              <p class="text-[10px] text-zinc-700 font-mono mt-1 truncate">{skill.source_dir}</p>
            {/if}
          </div>
          <button class="btn-danger shrink-0" onclick={() => remove(skill.name)}>
            <Trash2 class="w-3.5 h-3.5" />
          </button>
        </div>
      {/each}
    </div>
  {/if}
</div>
