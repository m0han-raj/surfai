/**
 * The line SurfAI says when the page under the conversation changes.
 *
 * Written here rather than asked of the model. A model told to announce
 * something announces it most of the time, and a notice that is usually there
 * is worse than none: you cannot rely on its absence meaning anything. This
 * one is always present when the page changed and never present when it did
 * not.
 *
 * It lives at the tail of the transcript and updates in place while you browse,
 * so hunting through five tabs leaves one line rather than five. The moment you
 * ask something, the message lands underneath it and the notice becomes a
 * permanent part of the conversation, which is exactly when it is worth
 * keeping: it marks where the subject changed.
 */

import type { ChatMessage } from '../types/agent';

let counter = 0;

function noticeFor(domain: string, at: number): ChatMessage {
  counter += 1;
  return {
    id: `n${at}-${counter}`,
    role: 'notice',
    domain,
    content: `Switched to ${domain}. I'll answer about this page from now on.`,
    at,
  };
}

/**
 * The transcript with its trailing context notice brought up to date.
 *
 * `anchor` is the page the conversation is currently about: the page the last
 * message was sent against. Returns the same array when nothing needs to
 * change, so React can skip the render.
 */
export function withContextNotice(
  messages: ChatMessage[],
  domain: string | undefined,
  anchor: string,
  at: number,
): ChatMessage[] {
  const trailing = messages[messages.length - 1];
  const hasTrailingNotice = trailing?.role === 'notice';

  // A tab with no readable page has no name to offer, and "switched to
  // nothing" says less than staying quiet. Chrome's own pages land here.
  if (!domain) {
    return hasTrailingNotice ? messages.slice(0, -1) : messages;
  }

  // Nothing to re-anchor before the conversation has started, and the header
  // chip already names the page.
  if (!messages.length) return messages;

  if (domain === anchor) {
    // Back where we started. Withdrawing the notice is the honest move: by
    // the time the next question is asked, nothing will have changed.
    return hasTrailingNotice ? messages.slice(0, -1) : messages;
  }

  if (hasTrailingNotice) {
    if (trailing.domain === domain) return messages;
    // Replace rather than append: only the latest is true.
    return [...messages.slice(0, -1), noticeFor(domain, at)];
  }

  return [...messages, noticeFor(domain, at)];
}
