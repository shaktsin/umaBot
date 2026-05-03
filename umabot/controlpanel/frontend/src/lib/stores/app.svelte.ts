export type PanelId =
  | 'dashboard'
  | 'chat'
  | 'connectors'
  | 'skills'
  | 'tasks'
  | 'agent_teams'
  | 'policy'
  | 'providers'
  | 'mcp'
  | 'config'
  | 'logs';

class AppStore {
  activePanel = $state<PanelId>('dashboard');
  pendingCount = $state(0);
  gatewayConnected = $state(false);

  navigate(panel: PanelId) {
    this.activePanel = panel;
  }
}

export const appStore = new AppStore();
