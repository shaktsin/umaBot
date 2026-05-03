import type { Attachment, PendingConfirmation } from '$lib/types';
import { appStore } from '$lib/stores/app.svelte';
import { chatStore } from '$lib/stores/chat.svelte';
import { multiAgentStore } from '$lib/stores/multiAgent.svelte';

type WsEvent =
  | { type: 'chat'; role: string; content: string; chat_id: string; attachments?: Attachment[] }
  | { type: 'event'; name: string; data: unknown }
  | { type: 'ping' };

function asRecord(data: unknown): Record<string, unknown> {
  if (data && typeof data === 'object' && !Array.isArray(data)) {
    return data as Record<string, unknown>;
  }
  return {};
}

class WsStore {
  connected = $state(false);
  pendingConfirmations = $state<PendingConfirmation[]>([]);

  private socket: WebSocket | null = null;
  private shouldReconnect = true;

  connect() {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${proto}//${window.location.host}/api/ws`;
    const ws = new WebSocket(url);
    this.socket = ws;

    ws.onopen = () => {
      this.connected = true;
    };

    ws.onclose = () => {
      this.connected = false;
      this.socket = null;
      if (this.shouldReconnect) {
        setTimeout(() => this.connect(), 3000);
      }
    };

    ws.onerror = () => {
      ws.close();
    };

    ws.onmessage = (e: MessageEvent) => {
      try {
        const msg = JSON.parse(e.data as string) as WsEvent;
        this.handle(msg);
      } catch {
        // ignore
      }
    };
  }

  private handle(msg: WsEvent) {
    if (msg.type === 'ping') return;

    if (msg.type === 'chat') {
      chatStore.removeLastPending();
      chatStore.addMessage({
        role: 'assistant',
        content: msg.content,
        attachments: msg.attachments,
      });
      chatStore.sending = false;
      return;
    }

    if (msg.type === 'event') {
      if (
        msg.name.startsWith('multi_agent_') ||
        msg.name.startsWith('team.')
      ) {
        multiAgentStore.handleEvent(msg.name, msg.data);
        return;
      }
      const data = asRecord(msg.data);
      if (msg.name === 'gateway_status') {
        appStore.gatewayConnected = !!data.connected;
      } else if (msg.name === 'pending_confirmation') {
        const confirm = data as unknown as PendingConfirmation;
        this.pendingConfirmations = [...this.pendingConfirmations, confirm];
        appStore.pendingCount = this.pendingConfirmations.length;
      } else if (msg.name === 'confirmation_resolved') {
        const { token } = data as { token: string };
        this.pendingConfirmations = this.pendingConfirmations.filter((c) => c.token !== token);
        appStore.pendingCount = this.pendingConfirmations.length;
      }
    }
  }

  send(text: string) {
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify({ type: 'chat', text }));
    }
  }

  disconnect() {
    this.shouldReconnect = false;
    this.socket?.close();
  }
}

export const wsStore = new WsStore();
