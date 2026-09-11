/**
 * Content script entry point.
 *
 * Injected on demand by the service worker (never declared for `<all_urls>`),
 * it exposes exactly two capabilities to the rest of the extension: capture a
 * semantic snapshot, and execute one validated action. Nothing else in SurfAI
 * can reach the page.
 */

import { executeAction, DEFAULT_TIMEOUT_MS } from './action-executor';
import { capturePage, DEFAULT_MAX_ELEMENTS } from './semantic-dom';
import { documentReady } from './page-observer';
import type {
  ExtensionMessage,
  MessageResponse,
} from '../types/messages';

/** Guards against double injection when the worker re-injects on a retry. */
declare global {
  interface Window {
    __surfaiContentLoaded?: boolean;
  }
}

function handleMessage(
  message: ExtensionMessage,
  sendResponse: (response: MessageResponse) => void,
): boolean {
  switch (message?.type) {
    case 'PING':
      sendResponse({ ok: true, data: { ready: true, url: window.location.href } });
      return false;

    case 'CAPTURE_PAGE':
      documentReady()
        .then(() => {
          const page = capturePage({
            maxElements: message.maxElements ?? DEFAULT_MAX_ELEMENTS,
            maxTextChars: message.maxTextChars,
          });
          sendResponse({ ok: true, data: page });
        })
        .catch((error: unknown) => {
          sendResponse({
            ok: false,
            error: error instanceof Error ? error.message : 'Failed to read the page',
          });
        });
      return true; // response is async

    case 'EXECUTE_ACTION':
      executeAction(message.action, message.timeoutMs ?? DEFAULT_TIMEOUT_MS)
        .then((result) => sendResponse({ ok: true, data: result }))
        .catch((error: unknown) => {
          // executeAction is designed not to throw; this is a last resort.
          sendResponse({
            ok: false,
            error: error instanceof Error ? error.message : 'Action failed',
          });
        });
      return true;

    default:
      sendResponse({ ok: false, error: `Unknown message type '${String(message?.type)}'` });
      return false;
  }
}

if (!window.__surfaiContentLoaded) {
  window.__surfaiContentLoaded = true;

  chrome.runtime.onMessage.addListener(
    (message: ExtensionMessage, _sender, sendResponse) =>
      handleMessage(message, sendResponse),
  );
}

export {};
