import type { ChatMessage } from '$lib/types';
import { api } from '$lib/api';

class ChatStore {
  messages = $state<ChatMessage[]>([]);
  loading = $state(false);
  sending = $state(false);

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
    this.messages = [...this.messages, msg];
  }

  removeLastPending() {
    this.messages = this.messages.filter((m) => !m.pending);
  }
}

export const chatStore = new ChatStore();
