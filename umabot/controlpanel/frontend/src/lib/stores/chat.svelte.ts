import type { ChatMessage } from '$lib/types';
import { api } from '$lib/api';

class ChatStore {
  messages = $state<ChatMessage[]>([]);
  loading = $state(false);
  sending = $state(false);
  private localSeq = 0;

  async loadHistory() {
    this.loading = true;
    try {
      const data = await api.getChatHistory() as ChatMessage[];
      this.messages = data;
    } catch (e) {
      console.error('Failed to load chat history', e);
    } finally {
      this.loading = false;
    }
  }

  addMessage(msg: ChatMessage) {
    const normalized: ChatMessage = { ...msg };
    if (!normalized.created_at) {
      normalized.created_at = new Date().toISOString();
    }
    if (typeof normalized.id !== 'number') {
      // Keep local WS-only messages uniquely keyed in UI.
      normalized.id = -Math.floor(Date.now() * 1000 + (this.localSeq++ % 1000));
    }
    this.messages = [...this.messages, normalized];
  }

  removeLastPending() {
    this.messages = this.messages.filter((m) => !m.pending);
  }
}

export const chatStore = new ChatStore();
