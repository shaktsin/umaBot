<script lang="ts">
  import { api } from '$lib/api';
  import type { MCPMethod, MCPServerInfo, MCPServerTestResponse } from '$lib/types';
  import { RefreshCw, Plus, Save, Trash2, FlaskConical, Wrench } from 'lucide-svelte';

  type MCPForm = {
    name: string;
    transport: string;
    command: string;
    argsText: string;
    envText: string;
    envVarsText: string;
    cwd: string;
    url: string;
    bearerTokenEnvVar: string;
    httpHeadersText: string;
    envHttpHeadersText: string;
    enabled: boolean;
    required: boolean;
    startupTimeoutSec: string;
    toolTimeoutSec: string;
    enabledToolsText: string;
    disabledToolsText: string;
    oauthCallbackPort: string;
    oauthCallbackUrl: string;
  };

  let servers = $state<MCPServerInfo[]>([]);
  let selectedName = $state('');
  let createMode = $state(false);
  let form = $state<MCPForm>(blankForm());
  let loading = $state(true);
  let saving = $state(false);
  let testing = $state(false);
  let deleting = $state(false);
  let error = $state('');
  let notice = $state('');
  let methods = $state<MCPMethod[]>([]);
  let testResult = $state<MCPServerTestResponse | null>(null);
  let methodsLoading = $state(false);

  function blankForm(): MCPForm {
    return {
      name: '',
      transport: 'stdio',
      command: '',
      argsText: '',
      envText: '',
      envVarsText: '',
      cwd: '',
      url: '',
      bearerTokenEnvVar: '',
      httpHeadersText: '',
      envHttpHeadersText: '',
      enabled: true,
      required: false,
      startupTimeoutSec: '10',
      toolTimeoutSec: '60',
      enabledToolsText: '',
      disabledToolsText: '',
      oauthCallbackPort: '0',
      oauthCallbackUrl: '',
    };
  }

  function splitCsv(text: string): string[] {
    return text
      .split(',')
      .map((item) => item.trim())
      .filter((item, idx, arr) => item.length > 0 && arr.indexOf(item) === idx);
  }

  function splitLines(text: string): string[] {
    return text
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean);
  }

  function parseKeyValueLines(text: string, separator: ':' | '='): Record<string, string> {
    const out: Record<string, string> = {};
    for (const line of splitLines(text)) {
      const idx = line.indexOf(separator);
      if (idx <= 0) continue;
      const key = line.slice(0, idx).trim();
      const value = line.slice(idx + 1).trim();
      if (!key) continue;
      out[key] = value;
    }
    return out;
  }

  function formatKeyValue(data: Record<string, string>, separator: ':' | '='): string {
    return Object.entries(data)
      .map(([k, v]) => `${k}${separator} ${v}`)
      .join('\n');
  }

  function formFromServer(server: MCPServerInfo): MCPForm {
    return {
      name: server.name,
      transport: server.transport || 'stdio',
      command: server.command || '',
      argsText: (server.args || []).join('\n'),
      envText: formatKeyValue(server.env || {}, '='),
      envVarsText: (server.env_vars || []).join(', '),
      cwd: server.cwd || '',
      url: server.url || '',
      bearerTokenEnvVar: server.bearer_token_env_var || '',
      httpHeadersText: formatKeyValue(server.http_headers || {}, ':'),
      envHttpHeadersText: formatKeyValue(server.env_http_headers || {}, '='),
      enabled: !!server.enabled,
      required: !!server.required,
      startupTimeoutSec: String(server.startup_timeout_sec ?? 10),
      toolTimeoutSec: String(server.tool_timeout_sec ?? 60),
      enabledToolsText: (server.enabled_tools || []).join(', '),
      disabledToolsText: (server.disabled_tools || []).join(', '),
      oauthCallbackPort: String(server.mcp_oauth_callback_port ?? 0),
      oauthCallbackUrl: server.mcp_oauth_callback_url || '',
    };
  }

  function payloadFromForm(f: MCPForm): Record<string, unknown> {
    return {
      name: f.name.trim(),
      transport: f.transport,
      command: f.command.trim(),
      args: splitLines(f.argsText),
      env: parseKeyValueLines(f.envText, '='),
      env_vars: splitCsv(f.envVarsText),
      cwd: f.cwd.trim(),
      url: f.url.trim(),
      bearer_token_env_var: f.bearerTokenEnvVar.trim(),
      http_headers: parseKeyValueLines(f.httpHeadersText, ':'),
      env_http_headers: parseKeyValueLines(f.envHttpHeadersText, '='),
      enabled: f.enabled,
      required: f.required,
      startup_timeout_sec: Number(f.startupTimeoutSec || '10'),
      tool_timeout_sec: Number(f.toolTimeoutSec || '60'),
      enabled_tools: splitCsv(f.enabledToolsText),
      disabled_tools: splitCsv(f.disabledToolsText),
      mcp_oauth_callback_port: Number(f.oauthCallbackPort || '0'),
      mcp_oauth_callback_url: f.oauthCallbackUrl.trim(),
    };
  }

  async function load(selectName = selectedName) {
    loading = true;
    error = '';
    try {
      const data = await api.getMcpServers();
      servers = data.servers;

      if (createMode) {
        loading = false;
        return;
      }

      const target = servers.find((item) => item.name === selectName) || servers[0];
      if (target) {
        selectedName = target.name;
        form = formFromServer(target);
      } else {
        selectedName = '';
        form = blankForm();
      }
    } catch (e) {
      error = (e as Error).message;
    } finally {
      loading = false;
    }
  }

  function selectServer(name: string) {
    const target = servers.find((item) => item.name === name);
    if (!target) return;
    createMode = false;
    selectedName = name;
    form = formFromServer(target);
    methods = [];
    testResult = null;
  }

  function newServer() {
    createMode = true;
    selectedName = '';
    form = blankForm();
    methods = [];
    testResult = null;
    error = '';
    notice = '';
  }

  async function save() {
    saving = true;
    error = '';
    notice = '';
    try {
      const payload = payloadFromForm(form);
      if (createMode) {
        await api.createMcpServer(payload);
        notice = `MCP server '${String(payload.name)}' created`;
        createMode = false;
        selectedName = String(payload.name);
      } else if (selectedName) {
        await api.updateMcpServer(selectedName, payload);
        notice = `MCP server '${selectedName}' saved`;
      } else {
        throw new Error('Select a server first');
      }
      await load(createMode ? '' : selectedName || String(payload.name));
    } catch (e) {
      error = (e as Error).message;
    } finally {
      saving = false;
    }
  }

  async function removeServer() {
    if (createMode || !selectedName) return;
    if (!confirm(`Delete MCP server '${selectedName}'?`)) return;
    deleting = true;
    error = '';
    notice = '';
    try {
      await api.deleteMcpServer(selectedName);
      notice = `MCP server '${selectedName}' deleted`;
      selectedName = '';
      form = blankForm();
      methods = [];
      testResult = null;
      await load('');
    } catch (e) {
      error = (e as Error).message;
    } finally {
      deleting = false;
    }
  }

  async function testServer() {
    if (createMode || !selectedName) return;
    testing = true;
    error = '';
    notice = '';
    try {
      testResult = await api.testMcpServer(selectedName);
      notice = testResult.ok
        ? `Test passed. Discovered ${testResult.method_count} method(s).`
        : `Test failed: ${testResult.error ?? 'Unknown error'}`;
    } catch (e) {
      error = (e as Error).message;
      testResult = null;
    } finally {
      testing = false;
    }
  }

  async function loadMethods() {
    if (createMode || !selectedName) return;
    methodsLoading = true;
    error = '';
    try {
      const response = await api.getMcpServerMethods(selectedName);
      methods = response.methods || [];
      if (response.error) {
        error = response.error;
      }
    } catch (e) {
      error = (e as Error).message;
      methods = [];
    } finally {
      methodsLoading = false;
    }
  }

  $effect(() => { load(); });
</script>

<div class="space-y-4">
  <div class="flex items-center justify-between">
    <h1 class="section-title mb-0">MCP Servers</h1>
    <div class="flex items-center gap-2">
      <button class="btn-ghost" onclick={() => load()}>
        <RefreshCw class="w-3.5 h-3.5" /> Refresh
      </button>
      <button class="btn-primary" onclick={newServer}>
        <Plus class="w-3.5 h-3.5" /> New Server
      </button>
    </div>
  </div>

  {#if error}
    <div class="card p-3 border-red-500/20 bg-red-500/5 text-red-400 text-sm">{error}</div>
  {/if}
  {#if notice}
    <div class="card p-3 border-emerald-500/20 bg-emerald-500/5 text-emerald-400 text-sm">{notice}</div>
  {/if}

  <div class="grid grid-cols-1 xl:grid-cols-[280px_minmax(0,1fr)] gap-4">
    <div class="card p-3 space-y-2">
      <p class="label">Configured Servers</p>
      {#if loading}
        <div class="space-y-2">{#each Array(3) as _}<div class="h-9 rounded-lg bg-zinc-800 animate-pulse"></div>{/each}</div>
      {:else if servers.length === 0}
        <p class="text-xs text-zinc-500">No MCP servers configured.</p>
      {:else}
        {#each servers as server}
          <button
            class="w-full text-left rounded-lg border px-3 py-2 text-sm transition-colors {selectedName === server.name && !createMode ? 'border-violet-500/40 bg-violet-500/10 text-zinc-100' : 'border-zinc-800 bg-zinc-900 text-zinc-300 hover:border-zinc-700'}"
            onclick={() => selectServer(server.name)}
          >
            <div class="flex items-center justify-between gap-2">
              <span class="font-medium truncate">{server.name}</span>
              <span class="text-[10px] text-zinc-500">{server.transport}</span>
            </div>
          </button>
        {/each}
      {/if}
    </div>

    <div class="card p-5 space-y-4">
      <div class="flex items-center justify-between">
        <h2 class="text-sm font-semibold text-zinc-300 border-b border-zinc-800 pb-3 flex-1">
          {createMode ? 'Create MCP Server' : selectedName ? `Edit MCP Server: ${selectedName}` : 'MCP Server Editor'}
        </h2>
      </div>

      <div class="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <div>
          <label class="label block mb-1.5" for="mcp-name">Name</label>
          <input
            id="mcp-name"
            class="input w-full"
            value={form.name}
            disabled={!createMode}
            oninput={(e) => (form.name = (e.currentTarget as HTMLInputElement).value)}
          />
        </div>
        <div>
          <label class="label block mb-1.5" for="mcp-transport">Transport</label>
          <select id="mcp-transport" class="input w-full" bind:value={form.transport}>
            <option value="stdio">stdio</option>
            <option value="http">http</option>
          </select>
        </div>

        {#if form.transport === 'stdio'}
          <div>
            <label class="label block mb-1.5" for="mcp-command">Startup Command</label>
            <input id="mcp-command" class="input w-full" value={form.command} oninput={(e) => (form.command = (e.currentTarget as HTMLInputElement).value)} />
          </div>
          <div>
            <label class="label block mb-1.5" for="mcp-cwd">Working Directory (cwd)</label>
            <input id="mcp-cwd" class="input w-full" value={form.cwd} oninput={(e) => (form.cwd = (e.currentTarget as HTMLInputElement).value)} />
          </div>
          <div class="lg:col-span-2">
            <label class="label block mb-1.5" for="mcp-args">Args (one per line)</label>
            <textarea id="mcp-args" class="input w-full min-h-20" value={form.argsText} oninput={(e) => (form.argsText = (e.currentTarget as HTMLTextAreaElement).value)}></textarea>
          </div>
          <div class="lg:col-span-2">
            <label class="label block mb-1.5" for="mcp-env">Env Vars (KEY=VALUE per line)</label>
            <textarea id="mcp-env" class="input w-full min-h-24 font-mono text-xs" value={form.envText} oninput={(e) => (form.envText = (e.currentTarget as HTMLTextAreaElement).value)}></textarea>
          </div>
          <div class="lg:col-span-2">
            <label class="label block mb-1.5" for="mcp-env-vars">Forward Env Names (comma-separated)</label>
            <input id="mcp-env-vars" class="input w-full" value={form.envVarsText} oninput={(e) => (form.envVarsText = (e.currentTarget as HTMLInputElement).value)} />
          </div>
        {:else}
          <div class="lg:col-span-2">
            <label class="label block mb-1.5" for="mcp-url">HTTP URL</label>
            <input id="mcp-url" class="input w-full" value={form.url} oninput={(e) => (form.url = (e.currentTarget as HTMLInputElement).value)} />
          </div>
          <div>
            <label class="label block mb-1.5" for="mcp-bearer">Bearer Token Env Var</label>
            <input id="mcp-bearer" class="input w-full" value={form.bearerTokenEnvVar} oninput={(e) => (form.bearerTokenEnvVar = (e.currentTarget as HTMLInputElement).value)} />
          </div>
          <div>
            <label class="label block mb-1.5" for="mcp-oauth-port">OAuth Callback Port</label>
            <input id="mcp-oauth-port" class="input w-full" value={form.oauthCallbackPort} oninput={(e) => (form.oauthCallbackPort = (e.currentTarget as HTMLInputElement).value)} />
          </div>
          <div class="lg:col-span-2">
            <label class="label block mb-1.5" for="mcp-oauth-url">OAuth Callback URL (optional)</label>
            <input id="mcp-oauth-url" class="input w-full" value={form.oauthCallbackUrl} oninput={(e) => (form.oauthCallbackUrl = (e.currentTarget as HTMLInputElement).value)} />
          </div>
          <div class="lg:col-span-2">
            <label class="label block mb-1.5" for="mcp-http-headers">HTTP Headers (Header: value per line)</label>
            <textarea id="mcp-http-headers" class="input w-full min-h-20 font-mono text-xs" value={form.httpHeadersText} oninput={(e) => (form.httpHeadersText = (e.currentTarget as HTMLTextAreaElement).value)}></textarea>
          </div>
          <div class="lg:col-span-2">
            <label class="label block mb-1.5" for="mcp-env-http-headers">Env HTTP Headers (Header=ENV_VAR per line)</label>
            <textarea id="mcp-env-http-headers" class="input w-full min-h-20 font-mono text-xs" value={form.envHttpHeadersText} oninput={(e) => (form.envHttpHeadersText = (e.currentTarget as HTMLTextAreaElement).value)}></textarea>
          </div>
        {/if}

        <div>
          <label class="label block mb-1.5" for="mcp-startup-timeout">Startup Timeout (sec)</label>
          <input id="mcp-startup-timeout" class="input w-full" value={form.startupTimeoutSec} oninput={(e) => (form.startupTimeoutSec = (e.currentTarget as HTMLInputElement).value)} />
        </div>
        <div>
          <label class="label block mb-1.5" for="mcp-tool-timeout">Tool Timeout (sec)</label>
          <input id="mcp-tool-timeout" class="input w-full" value={form.toolTimeoutSec} oninput={(e) => (form.toolTimeoutSec = (e.currentTarget as HTMLInputElement).value)} />
        </div>
        <div>
          <label class="label block mb-1.5" for="mcp-enabled-tools">Enabled Tools (comma-separated globs)</label>
          <input id="mcp-enabled-tools" class="input w-full" value={form.enabledToolsText} oninput={(e) => (form.enabledToolsText = (e.currentTarget as HTMLInputElement).value)} />
        </div>
        <div>
          <label class="label block mb-1.5" for="mcp-disabled-tools">Disabled Tools (comma-separated globs)</label>
          <input id="mcp-disabled-tools" class="input w-full" value={form.disabledToolsText} oninput={(e) => (form.disabledToolsText = (e.currentTarget as HTMLInputElement).value)} />
        </div>
      </div>

      <div class="flex items-center justify-between">
        <div class="flex items-center gap-4">
          <label class="inline-flex items-center gap-2 text-sm text-zinc-300">
            <input type="checkbox" checked={form.enabled} onchange={(e) => (form.enabled = (e.currentTarget as HTMLInputElement).checked)} />
            enabled
          </label>
          <label class="inline-flex items-center gap-2 text-sm text-zinc-300">
            <input type="checkbox" checked={form.required} onchange={(e) => (form.required = (e.currentTarget as HTMLInputElement).checked)} />
            required
          </label>
        </div>
        <div class="flex items-center gap-2">
          {#if !createMode && selectedName}
            <button class="btn-ghost" onclick={testServer} disabled={testing}>
              <FlaskConical class="w-3.5 h-3.5" /> {testing ? 'Testing…' : 'Test Connection'}
            </button>
          {/if}
          <button class="btn-primary" onclick={save} disabled={saving}>
            <Save class="w-3.5 h-3.5" /> {saving ? 'Saving…' : createMode ? 'Create Server' : 'Save Changes'}
          </button>
          {#if !createMode && selectedName}
            <button class="btn-danger" onclick={removeServer} disabled={deleting}>
              <Trash2 class="w-3.5 h-3.5" /> {deleting ? 'Deleting…' : 'Delete'}
            </button>
          {/if}
        </div>
      </div>

      {#if testResult}
        <div class="card p-3 border border-zinc-800 bg-zinc-950/60">
          <p class="text-sm font-medium {testResult.ok ? 'text-emerald-400' : 'text-red-400'}">
            {testResult.ok ? 'Test Passed' : 'Test Failed'}
          </p>
          <p class="text-xs text-zinc-500 mt-1">Discovered methods: {testResult.method_count}</p>
          {#if testResult.error}
            <p class="text-xs text-red-400 mt-1">{testResult.error}</p>
          {/if}
        </div>
      {/if}

      {#if !createMode && selectedName}
        <div class="space-y-2">
          <div class="flex items-center justify-between">
            <p class="text-sm font-medium text-zinc-300">Methods</p>
            <button class="btn-ghost" onclick={loadMethods}>
              <Wrench class="w-3.5 h-3.5" /> Load Methods
            </button>
          </div>
          {#if methodsLoading}
            <p class="text-xs text-zinc-500">Loading methods…</p>
          {:else if methods.length === 0}
            <p class="text-xs text-zinc-500">No methods loaded.</p>
          {:else}
            <div class="space-y-2">
              {#each methods as method}
                <div class="rounded-lg border border-zinc-800 bg-zinc-950/50 p-3">
                  <p class="text-sm font-medium text-zinc-200">{method.name}</p>
                  <p class="text-[11px] text-zinc-600 font-mono mt-1">{method.prefixed_name}</p>
                  {#if method.description}
                    <p class="text-xs text-zinc-500 mt-2">{method.description}</p>
                  {/if}
                </div>
              {/each}
            </div>
          {/if}
        </div>
      {/if}
    </div>
  </div>
</div>
