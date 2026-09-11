/**
 * Service worker: the broker between the side panel and the page.
 *
 * The side panel cannot talk to a tab directly, and a content script is not
 * present until something injects it. This worker owns both concerns:
 * on-demand injection (so SurfAI never needs `<all_urls>`) and message relay
 * with a clear error whenever the page is one Chrome forbids scripting.
 *
 * It holds no secrets: the LLM key lives only on the backend.
 */

import type {
  ExtensionMessage,
  MessageResponse,
  TabContext,
} from '../types/messages';

const CONTENT_SCRIPT = 'content.js';
const INJECT_TIMEOUT_MS = 5000;

/** Pages where Chrome refuses content-script injection. */
const RESTRICTED_PREFIXES = [
  'chrome://',
  'chrome-extension://',
  'edge://',
  'about:',
  'devtools://',
  'view-source:',
  'https://chromewebstore.google.com',
  'https://chrome.google.com/webstore',
];

function isRestricted(url: string | undefined): boolean {
  if (!url) return true;
  return RESTRICTED_PREFIXES.some((prefix) => url.startsWith(prefix));
}

function restrictedMessage(url: string | undefined): string {
  if (!url) return 'No active tab was found.';
  if (url.startsWith('chrome://') || url.startsWith('edge://') || url.startsWith('about:')) {
    return 'SurfAI cannot read browser settings pages. Open a website and try again.';
  }
  if (url.includes('chromewebstore') || url.includes('chrome.google.com/webstore')) {
    return 'Chrome blocks extensions from reading the Web Store. Open another site and try again.';
  }
  return 'SurfAI cannot read this page. Open a regular website and try again.';
}

/** Open the side panel when the toolbar icon is clicked. */
chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel
    .setPanelBehavior({ openPanelOnActionClick: true })
    .catch((error) => console.warn('[SurfAI] Could not set panel behavior', error));
});

chrome.action?.onClicked.addListener((tab) => {
  if (tab.windowId !== undefined) {
    chrome.sidePanel.open({ windowId: tab.windowId }).catch((error) => {
      console.warn('[SurfAI] Could not open side panel', error);
    });
  }
});

async function getActiveTab(): Promise<chrome.tabs.Tab | undefined> {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

/** Is a SurfAI content script already listening in this tab? */
async function isContentScriptReady(tabId: number): Promise<boolean> {
  try {
    const response = await Promise.race([
      chrome.tabs.sendMessage(tabId, { type: 'PING' }),
      new Promise((resolve) => setTimeout(() => resolve(null), 500)),
    ]);
    return Boolean((response as MessageResponse | null)?.ok);
  } catch {
    // No receiver: the script has not been injected into this document yet.
    return false;
  }
}

/**
 * Ensure the content script is running, injecting it if needed.
 *
 * Injection is per-document, so this has to run again after every navigation --
 * which is exactly why it is checked before each message rather than once.
 */
async function ensureContentScript(tabId: number, url?: string): Promise<void> {
  if (isRestricted(url)) {
    throw new Error(restrictedMessage(url));
  }

  if (await isContentScriptReady(tabId)) return;

  try {
    await chrome.scripting.executeScript({
      target: { tabId, allFrames: false },
      files: [CONTENT_SCRIPT],
    });
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    if (/cannot be scripted|Extension manifest must request permission/i.test(detail)) {
      throw new Error(restrictedMessage(url));
    }
    throw new Error(`SurfAI could not attach to this page: ${detail}`);
  }

  // The listener registers synchronously on injection, but give the document a
  // moment on very slow pages before declaring failure.
  const deadline = Date.now() + INJECT_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (await isContentScriptReady(tabId)) return;
    await new Promise((resolve) => setTimeout(resolve, 150));
  }
  throw new Error('SurfAI attached to the page but it did not respond in time.');
}

/** Relay a message to the active tab, injecting first if necessary. */
async function relayToTab(
  message: ExtensionMessage,
  explicitTabId?: number,
): Promise<MessageResponse> {
  const tab = explicitTabId
    ? await chrome.tabs.get(explicitTabId)
    : await getActiveTab();

  if (!tab?.id) {
    return { ok: false, error: 'No active tab was found.' };
  }

  try {
    await ensureContentScript(tab.id, tab.url);
    const response = (await chrome.tabs.sendMessage(tab.id, message)) as MessageResponse;
    return response ?? { ok: false, error: 'The page did not respond.' };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : 'Could not reach the page.',
    };
  }
}

chrome.runtime.onMessage.addListener((message: ExtensionMessage & { tabId?: number }, sender, sendResponse) => {
  // Only the side panel talks to the worker; a page must never drive it.
  if (sender.tab) {
    sendResponse({ ok: false, error: 'Messages from page context are not accepted.' });
    return false;
  }

  if (message?.type === 'GET_TAB_CONTEXT') {
    getActiveTab()
      .then((tab) => {
        if (!tab) {
          sendResponse({ ok: false, error: 'No active tab was found.' });
          return;
        }
        const context: TabContext = {
          url: tab.url ?? '',
          title: tab.title ?? '',
          tab_id: tab.id,
        };
        sendResponse({
          ok: true,
          data: context,
          // Lets the panel explain *why* before the user asks for anything.
          ...(isRestricted(tab.url) ? { error: restrictedMessage(tab.url) } : {}),
        });
      })
      .catch((error: unknown) => {
        sendResponse({
          ok: false,
          error: error instanceof Error ? error.message : 'Could not read the tab.',
        });
      });
    return true;
  }

  relayToTab(message, message.tabId).then(sendResponse);
  return true; // async response
});

export {};
