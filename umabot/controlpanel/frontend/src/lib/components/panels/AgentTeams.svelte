<script lang="ts">
  import { onMount } from 'svelte';
  import { Plus, Save, Trash2, RefreshCw, Play, FlaskConical, Wand2, Download } from 'lucide-svelte';
  import { api, timeAgo } from '$lib/api';
  import type { AgentSkillItem, AgentTeam, AgentTeamMember, AgentTeamRoute, AgentTeamRun } from '$lib/types';

  type RouteResult = Record<string, unknown> | null;

  let loading = $state(true);
  let saving = $state(false);
  let error = $state('');
  let success = $state('');

  let teams = $state<AgentTeam[]>([]);
  let skills = $state<AgentSkillItem[]>([]);
  let runs = $state<AgentTeamRun[]>([]);

  let selectedTeamId = $state<string | null>(null);
  let teamDraft = $state<AgentTeam>(blankTeam());

  let buildPrompt = $state('');
  let routeTask = $state('');
  let routeResult = $state<RouteResult>(null);
  let dryRunResult = $state<RouteResult>(null);

  let installSource = $state('');
  let installName = $state('');
  let installDir = $state('');
  let teamDirs = $state<string[]>([]);

  onMount(async () => {
    await loadAll();
  });

  async function loadAll() {
    loading = true;
    error = '';
    success = '';
    try {
      const [teamsData, skillsData, runsData, sources] = await Promise.all([
        api.getAgentTeams(),
        api.getAgentSkills(),
        api.getAgentTeamRuns(30),
        api.getAgentTeamSources(),
      ]);
      teams = teamsData.map(normalizeLoadedTeam);
      skills = skillsData;
      runs = runsData;
      installDir = String(sources.install_dir || '');
      teamDirs = Array.isArray(sources.team_dirs) ? (sources.team_dirs as string[]) : [];

      if (selectedTeamId !== null) {
        const found = teams.find((team) => team.id === selectedTeamId);
        if (found) {
          teamDraft = cloneTeam(found);
        } else {
          selectedTeamId = null;
          teamDraft = blankTeam();
        }
      }
    } catch (err) {
      error = (err as Error).message;
    } finally {
      loading = false;
    }
  }

  function blankTeam(): AgentTeam {
    return {
      id: '',
      name: '',
      description: '',
      enabled: true,
      priority: 0,
      team_type: 'orchestrator_worker',
      confidence_threshold: 0.62,
      fit_policy: {},
      budget_policy: {},
      retry_policy: {
        max_retries: 2,
        fail_on_defer: true,
        require_blockers_section: true,
        enforce_shell_success: true,
      },
      tool_pool: [],
      required_capabilities: [],
      capability_overrides: {},
      rules_markdown: '# Rules\n\nDefine hard constraints for this team.',
      worksteps_markdown: '# Worksteps\n\n1. Plan\n2. Execute\n3. Verify',
      members: [blankMember(0)],
      routes: [blankRoute()],
      writable: true,
    };
  }

  function blankMember(orderIndex: number): AgentTeamMember {
    return {
      role: 'Worker',
      objective_template: '',
      output_schema: {},
      model: '',
      tool_allowlist: [],
      skill_allowlist: [],
      workspace: '',
      order_index: orderIndex,
      max_tool_calls: 0,
      max_iterations: 0,
    };
  }

  function blankRoute(): AgentTeamRoute {
    return {
      route_type: 'keyword',
      pattern_or_hint: '',
      weight: 1,
    };
  }

  function normalizeLoadedTeam(team: AgentTeam): AgentTeam {
    return {
      ...cloneTeam(team),
      id: team.id || '',
      team_type: normalizeTeamType(team.team_type),
      members: (team.members || []).map((member, idx) => ({
        ...member,
        order_index: Number.isFinite(member.order_index) ? member.order_index : idx,
        tool_allowlist: [...(member.tool_allowlist || [])],
        skill_allowlist: [...(member.skill_allowlist || [])],
      })),
      routes: (team.routes || []).map((route) => ({
        ...route,
        route_type: route.route_type || 'keyword',
        pattern_or_hint: route.pattern_or_hint || '',
        weight: Number.isFinite(route.weight) ? route.weight : 1,
      })),
      tool_pool: [...(team.tool_pool || [])],
      required_capabilities: [...(team.required_capabilities || [])],
      capability_overrides: { ...(team.capability_overrides || {}) },
      rules_markdown: team.rules_markdown || '',
      worksteps_markdown: team.worksteps_markdown || '',
    };
  }

  function normalizeTeamType(value: string): AgentTeam['team_type'] {
    if (value === 'chain' || value === 'parallel' || value === 'orchestrator_worker' || value === 'hybrid') {
      return value;
    }
    return 'orchestrator_worker';
  }

  function cloneTeam(team: AgentTeam): AgentTeam {
    return JSON.parse(JSON.stringify(team)) as AgentTeam;
  }

  function splitCsv(value: string): string[] {
    return value
      .split(',')
      .map((part) => part.trim())
      .filter((part, index, arr) => part.length > 0 && arr.indexOf(part) === index);
  }

  function selectTeam(team: AgentTeam) {
    selectedTeamId = team.id || null;
    teamDraft = cloneTeam(team);
    routeResult = null;
    dryRunResult = null;
    success = '';
    error = '';
  }

  function createNewTeam() {
    selectedTeamId = null;
    teamDraft = blankTeam();
    routeResult = null;
    dryRunResult = null;
    success = '';
    error = '';
  }

  function addMember() {
    const next = cloneTeam(teamDraft);
    next.members.push(blankMember(next.members.length));
    teamDraft = next;
  }

  function removeMember(index: number) {
    const next = cloneTeam(teamDraft);
    next.members = next.members.filter((_, idx) => idx !== index);
    if (next.members.length === 0) {
      next.members = [blankMember(0)];
    }
    next.members.forEach((member, idx) => {
      member.order_index = idx;
    });
    teamDraft = next;
  }

  function addRoute() {
    const next = cloneTeam(teamDraft);
    next.routes.push(blankRoute());
    teamDraft = next;
  }

  function removeRoute(index: number) {
    const next = cloneTeam(teamDraft);
    next.routes = next.routes.filter((_, idx) => idx !== index);
    if (next.routes.length === 0) {
      next.routes = [blankRoute()];
    }
    teamDraft = next;
  }

  function setMemberSkill(memberIndex: number, skillKey: string, checked: boolean) {
    const next = cloneTeam(teamDraft);
    const member = next.members[memberIndex];
    const selected = new Set(member.skill_allowlist || []);
    if (checked) {
      selected.add(skillKey);
    } else {
      selected.delete(skillKey);
    }
    member.skill_allowlist = [...selected];
    teamDraft = next;
  }

  async function installTeamPack() {
    if (!installSource.trim()) return;
    saving = true;
    error = '';
    success = '';
    try {
      await api.installAgentTeam(installSource.trim(), installName.trim() || undefined);
      installSource = '';
      installName = '';
      await loadAll();
      success = 'Team pack installed.';
    } catch (err) {
      error = (err as Error).message;
    } finally {
      saving = false;
    }
  }

  async function saveTeam() {
    saving = true;
    error = '';
    success = '';
    try {
      const payload = cloneTeam(teamDraft);
      payload.members = payload.members.map((member, idx) => ({
        ...member,
        order_index: idx,
        tool_allowlist: [...(member.tool_allowlist || [])],
        skill_allowlist: [...(member.skill_allowlist || [])],
      }));

      if (selectedTeamId !== null) {
        const updated = normalizeLoadedTeam(await api.updateAgentTeam(selectedTeamId, payload));
        teams = teams.map((team) => (team.id === updated.id ? updated : team));
        teamDraft = cloneTeam(updated);
        success = `Updated team '${updated.name}'.`;
      } else {
        const created = normalizeLoadedTeam(await api.createAgentTeam(payload));
        teams = [created, ...teams];
        selectedTeamId = created.id || null;
        teamDraft = cloneTeam(created);
        success = `Created team '${created.name}'.`;
      }
    } catch (err) {
      error = (err as Error).message;
    } finally {
      saving = false;
    }
  }

  async function deleteTeam() {
    if (selectedTeamId === null) return;
    if (!window.confirm('Delete selected team?')) return;

    saving = true;
    error = '';
    success = '';
    try {
      await api.deleteAgentTeam(selectedTeamId);
      teams = teams.filter((team) => team.id !== selectedTeamId);
      selectedTeamId = null;
      teamDraft = blankTeam();
      success = 'Team deleted.';
    } catch (err) {
      error = (err as Error).message;
    } finally {
      saving = false;
    }
  }

  async function uninstallSelectedTeamPack() {
    if (selectedTeamId === null) return;
    if (!window.confirm('Remove installed team pack?')) return;
    saving = true;
    error = '';
    success = '';
    try {
      await api.uninstallAgentTeam(selectedTeamId);
      teams = teams.filter((team) => team.id !== selectedTeamId);
      selectedTeamId = null;
      teamDraft = blankTeam();
      success = 'Team pack removed.';
    } catch (err) {
      error = (err as Error).message;
    } finally {
      saving = false;
    }
  }

  async function buildFromPrompt() {
    if (!buildPrompt.trim()) return;
    saving = true;
    error = '';
    success = '';
    try {
      const draft = normalizeLoadedTeam(await api.buildAgentTeamFromPrompt(buildPrompt.trim()));
      teamDraft = draft;
      selectedTeamId = null;
      success = 'Generated draft from prompt. Review and save.';
    } catch (err) {
      error = (err as Error).message;
    } finally {
      saving = false;
    }
  }

  async function testRoute() {
    if (!routeTask.trim()) return;
    error = '';
    success = '';
    try {
      routeResult = await api.testAgentTeamRoute(routeTask.trim());
    } catch (err) {
      error = (err as Error).message;
      routeResult = null;
    }
  }

  async function dryRunSelectedTeam() {
    if (selectedTeamId === null || !routeTask.trim()) return;
    error = '';
    success = '';
    try {
      dryRunResult = await api.dryRunAgentTeam(selectedTeamId, routeTask.trim());
    } catch (err) {
      error = (err as Error).message;
      dryRunResult = null;
    }
  }

  function stringify(value: unknown): string {
    return JSON.stringify(value, null, 2);
  }
</script>

<div class="space-y-4">
  <div class="flex items-center justify-between gap-3">
    <h1 class="section-title mb-0">Agent Teams</h1>
    <div class="flex items-center gap-2">
      <button class="btn-ghost" onclick={() => loadAll()}>
        <RefreshCw class="w-3.5 h-3.5" /> Refresh
      </button>
      <button class="btn-primary" onclick={createNewTeam}>
        <Plus class="w-3.5 h-3.5" /> New Team
      </button>
    </div>
  </div>

  {#if error}
    <div class="card p-3 border-red-500/20 bg-red-500/5 text-red-400 text-sm">{error}</div>
  {/if}
  {#if success}
    <div class="card p-3 border-emerald-500/20 bg-emerald-500/5 text-emerald-400 text-sm">{success}</div>
  {/if}

  <div class="card p-4 space-y-3">
    <h2 class="text-sm font-semibold text-zinc-300">Team Pack Install</h2>
    <p class="text-xs text-zinc-500">Install dir: <span class="font-mono">{installDir}</span></p>
    {#if teamDirs.length > 0}
      <p class="text-xs text-zinc-500">Scanned dirs: {teamDirs.join(', ')}</p>
    {/if}
    <div class="grid grid-cols-1 lg:grid-cols-[minmax(0,2fr)_1fr_auto] gap-2">
      <input class="input" placeholder="Local path or git URL" bind:value={installSource} />
      <input class="input" placeholder="Install name (optional)" bind:value={installName} />
      <button class="btn-primary" onclick={installTeamPack} disabled={saving || !installSource.trim()}>
        <Download class="w-3.5 h-3.5" /> Install
      </button>
    </div>
  </div>

  <div class="grid grid-cols-1 xl:grid-cols-[300px_minmax(0,1fr)] gap-4">
    <div class="card p-3 space-y-3">
      <p class="label">Teams</p>
      {#if loading}
        <p class="text-xs text-zinc-500">Loading teams…</p>
      {:else if teams.length === 0}
        <p class="text-xs text-zinc-500">No teams yet.</p>
      {:else}
        <div class="space-y-2">
          {#each teams as team (team.id)}
            <button
              class="w-full text-left rounded-lg border px-3 py-2 transition-colors {team.id === selectedTeamId ? 'border-violet-500/60 bg-violet-500/10' : 'border-zinc-800 hover:border-zinc-700 hover:bg-zinc-900'}"
              onclick={() => selectTeam(team)}
            >
              <div class="flex items-center justify-between gap-2">
                <p class="text-sm font-medium text-zinc-200 truncate">{team.name}</p>
                <span class="text-[10px] uppercase text-zinc-500">{team.enabled ? 'enabled' : 'disabled'}</span>
              </div>
              <p class="text-[11px] text-zinc-500 mt-1">{team.team_type} • {team.id}</p>
            </button>
          {/each}
        </div>
      {/if}

      <div class="border-t border-zinc-800 pt-3 space-y-2">
        <p class="label">Recent Runs</p>
        {#if runs.length === 0}
          <p class="text-xs text-zinc-500">No runs recorded.</p>
        {:else}
          {#each runs.slice(0, 8) as run (run.run_id)}
            <div class="rounded-lg border border-zinc-800 bg-zinc-950/50 px-2.5 py-2">
              <p class="text-xs text-zinc-300 font-mono">{run.run_id}</p>
              <p class="text-[11px] text-zinc-500 mt-0.5">{run.status} • {run.complexity_class} • {timeAgo(run.started_at)}</p>
            </div>
          {/each}
        {/if}
      </div>
    </div>

    <div class="space-y-4">
      <div class="card p-4 space-y-4">
        <div class="flex items-center justify-between gap-3">
          <h2 class="text-sm font-semibold text-zinc-300">Team Editor</h2>
          <div class="flex items-center gap-2">
            {#if selectedTeamId !== null}
              <button class="btn-danger" onclick={deleteTeam} disabled={saving || !teamDraft.writable}>
                <Trash2 class="w-3.5 h-3.5" /> Delete
              </button>
            {/if}
            {#if selectedTeamId !== null && teamDraft.writable}
              <button class="btn-danger" onclick={uninstallSelectedTeamPack} disabled={saving}>
                <Trash2 class="w-3.5 h-3.5" /> Uninstall Pack
              </button>
            {/if}
            <button class="btn-primary" onclick={saveTeam} disabled={saving || (selectedTeamId !== null && !teamDraft.writable)}>
              <Save class="w-3.5 h-3.5" /> {saving ? 'Saving…' : 'Save Team'}
            </button>
          </div>
        </div>

        {#if selectedTeamId !== null && !teamDraft.writable}
          <p class="text-xs text-amber-400">This team is loaded from a read-only directory. Duplicate it via New Team if you want to modify.</p>
        {/if}

        <div class="grid grid-cols-1 lg:grid-cols-2 gap-3">
          <div>
            <label class="label block mb-1.5" for="team-id">ID</label>
            <input id="team-id" class="input w-full" bind:value={teamDraft.id} disabled={selectedTeamId !== null} />
          </div>
          <div>
            <label class="label block mb-1.5" for="team-name">Name</label>
            <input id="team-name" class="input w-full" bind:value={teamDraft.name} />
          </div>
          <div>
            <label class="label block mb-1.5" for="team-type">Team Type</label>
            <select id="team-type" class="input w-full" bind:value={teamDraft.team_type}>
              <option value="orchestrator_worker">orchestrator_worker</option>
              <option value="chain">chain</option>
              <option value="parallel">parallel</option>
              <option value="hybrid">hybrid</option>
            </select>
          </div>
          <div>
            <label class="label block mb-1.5" for="team-priority">Priority</label>
            <input id="team-priority" class="input w-full" type="number" bind:value={teamDraft.priority} />
          </div>
          <div>
            <label class="label block mb-1.5" for="team-threshold">Confidence Threshold</label>
            <input id="team-threshold" class="input w-full" type="number" min="0" max="1" step="0.01" bind:value={teamDraft.confidence_threshold} />
          </div>
          <div class="lg:col-span-2">
            <label class="label block mb-1.5" for="team-desc">Description</label>
            <textarea id="team-desc" class="input w-full min-h-20" bind:value={teamDraft.description}></textarea>
          </div>
          {#if teamDraft.source_dir}
            <div class="lg:col-span-2 text-xs text-zinc-500">source: <span class="font-mono">{teamDraft.source_dir}</span></div>
          {/if}
          <label class="inline-flex items-center gap-2 text-sm text-zinc-300 lg:col-span-2">
            <input type="checkbox" bind:checked={teamDraft.enabled} /> enabled
          </label>
        </div>

        <div class="space-y-3 border-t border-zinc-800 pt-3">
          <p class="text-sm font-medium text-zinc-300">Rules (Markdown)</p>
          <textarea class="input min-h-28" bind:value={teamDraft.rules_markdown}></textarea>
          <p class="text-sm font-medium text-zinc-300">Worksteps (Markdown)</p>
          <textarea class="input min-h-28" bind:value={teamDraft.worksteps_markdown}></textarea>
        </div>

        <div class="space-y-3 border-t border-zinc-800 pt-3">
          <div class="flex items-center justify-between">
            <p class="text-sm font-medium text-zinc-300">Members</p>
            <button class="btn-ghost" onclick={addMember}><Plus class="w-3.5 h-3.5" /> Add Member</button>
          </div>

          {#each teamDraft.members as member, idx}
            <div class="rounded-lg border border-zinc-800 bg-zinc-950/40 p-3 space-y-3">
              <div class="flex items-center justify-between">
                <p class="text-xs text-zinc-500 uppercase tracking-wide">Member #{idx + 1}</p>
                <button class="btn-danger" onclick={() => removeMember(idx)}><Trash2 class="w-3.5 h-3.5" /> Remove</button>
              </div>

              <div class="grid grid-cols-1 lg:grid-cols-2 gap-2">
                <input class="input" placeholder="Role" bind:value={member.role} />
                <input class="input" placeholder="Model (optional)" bind:value={member.model} />
                <input class="input" placeholder="Workspace" bind:value={member.workspace} />
                <input
                  class="input"
                  placeholder="Tools allowlist (comma-separated)"
                  value={member.tool_allowlist.join(', ')}
                  oninput={(event) => {
                    const target = event.currentTarget as HTMLInputElement;
                    member.tool_allowlist = splitCsv(target.value);
                    teamDraft = teamDraft;
                  }}
                />
                <input class="input" type="number" placeholder="Max tool calls" bind:value={member.max_tool_calls} />
                <input class="input" type="number" placeholder="Max iterations" bind:value={member.max_iterations} />
                <textarea class="input lg:col-span-2 min-h-16" placeholder="Objective template" bind:value={member.objective_template}></textarea>
              </div>

              <div>
                <p class="text-xs text-zinc-500 mb-2">Allowed skills</p>
                {#if skills.length === 0}
                  <p class="text-xs text-zinc-600">No skills loaded. Install from Skills panel.</p>
                {:else}
                  <div class="grid grid-cols-1 md:grid-cols-2 gap-1.5">
                    {#each skills as skill (skill.skill_key)}
                      <label class="inline-flex items-center gap-2 text-xs text-zinc-300">
                        <input
                          type="checkbox"
                          checked={member.skill_allowlist.includes(skill.skill_key)}
                          onchange={(event) => setMemberSkill(idx, skill.skill_key, (event.currentTarget as HTMLInputElement).checked)}
                        />
                        <span>{skill.name} <span class="text-zinc-500">({skill.skill_key})</span></span>
                      </label>
                    {/each}
                  </div>
                {/if}
              </div>
            </div>
          {/each}
        </div>

        <div class="space-y-3 border-t border-zinc-800 pt-3">
          <div class="flex items-center justify-between">
            <p class="text-sm font-medium text-zinc-300">Routes</p>
            <button class="btn-ghost" onclick={addRoute}><Plus class="w-3.5 h-3.5" /> Add Route</button>
          </div>
          {#each teamDraft.routes as route, idx}
            <div class="grid grid-cols-[1fr_2fr_120px_auto] gap-2 items-center">
              <select class="input" bind:value={route.route_type}>
                <option value="keyword">keyword</option>
                <option value="regex">regex</option>
                <option value="tag">tag</option>
                <option value="llm_router_hint">llm_router_hint</option>
              </select>
              <input class="input" placeholder="Pattern or hint" bind:value={route.pattern_or_hint} />
              <input class="input" type="number" step="0.1" bind:value={route.weight} />
              <button class="btn-danger" onclick={() => removeRoute(idx)}><Trash2 class="w-3.5 h-3.5" /></button>
            </div>
          {/each}
        </div>
      </div>

      <div class="card p-4 space-y-3">
        <h2 class="text-sm font-semibold text-zinc-300">Prompt Builder + Match Testing</h2>
        <div class="grid grid-cols-1 gap-2">
          <textarea class="input min-h-20" placeholder="Describe the team you want to generate..." bind:value={buildPrompt}></textarea>
          <button class="btn-ghost" onclick={buildFromPrompt} disabled={saving || !buildPrompt.trim()}>
            <Wand2 class="w-3.5 h-3.5" /> Build Team From Prompt
          </button>

          <textarea class="input min-h-20" placeholder="Task text to test routing and dry-run" bind:value={routeTask}></textarea>
          <div class="flex items-center gap-2">
            <button class="btn-ghost" onclick={testRoute} disabled={!routeTask.trim()}>
              <FlaskConical class="w-3.5 h-3.5" /> Test Route
            </button>
            <button class="btn-ghost" onclick={dryRunSelectedTeam} disabled={selectedTeamId === null || !routeTask.trim()}>
              <Play class="w-3.5 h-3.5" /> Dry Run Selected Team
            </button>
          </div>

          {#if routeResult}
            <div class="rounded-lg border border-zinc-800 bg-zinc-950/50 p-3">
              <p class="text-xs text-zinc-400 mb-2">Route Result</p>
              <pre class="text-xs text-zinc-300 whitespace-pre-wrap overflow-x-auto">{stringify(routeResult)}</pre>
            </div>
          {/if}

          {#if dryRunResult}
            <div class="rounded-lg border border-zinc-800 bg-zinc-950/50 p-3">
              <p class="text-xs text-zinc-400 mb-2">Dry Run Result</p>
              <pre class="text-xs text-zinc-300 whitespace-pre-wrap overflow-x-auto">{stringify(dryRunResult)}</pre>
            </div>
          {/if}
        </div>
      </div>
    </div>
  </div>
</div>
