/**
 * Turning a stored conversation back into a transcript the panel can show.
 *
 * The backend records roles and text; the panel renders a slightly richer
 * shape. Most of the translation is mechanical. The part that is not is the
 * page-change notice: it has to come back as a notice rather than as an
 * assistant turn, both because it reads differently and because a notice is
 * never sent to the model as history. Losing it would leave a thread whose
 * messages quietly start being about a different page for no visible reason.
 */

import type { StoredConversation } from '../services/api';
import type { ChatMessage } from '../types/agent';

function domainOf(url: string | null): string {
  if (!url) return '';
  try {
    return new URL(url).hostname;
  } catch {
    // A url we cannot parse is not worth losing the message over.
    return '';
  }
}

export function toChatMessages(conversation: StoredConversation): ChatMessage[] {
  return conversation.messages.map((message, index) => ({
    id: `s${conversation.id}-${index}`,
    role: message.role,
    content: message.content,
    at: message.created_at ? Date.parse(message.created_at) : Date.now(),
    warnings: message.warnings?.length ? message.warnings : undefined,
    ...(message.role === 'notice' ? { domain: domainOf(message.page_url) } : {}),
  }));
}

/**
 * The page the conversation was last about.
 *
 * Reopening a thread has to restore this, or the first tab switch afterwards
 * is measured against the wrong page: either announcing a change that already
 * happened, or staying silent about one that just did.
 */
export function anchorOf(conversation: StoredConversation): string {
  for (let i = conversation.messages.length - 1; i >= 0; i -= 1) {
    const domain = domainOf(conversation.messages[i].page_url);
    if (domain) return domain;
  }
  return '';
}
