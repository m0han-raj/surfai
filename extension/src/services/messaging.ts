/**
 * Side panel -> service worker messaging.
 *
 * Every call returns a discriminated result rather than throwing, because the
 * common failures here are expected conditions (restricted page, tab still
 * loading) that the UI should explain rather than crash on.
 */

import type { ActionResult, BrowserAction } from '@shared/action-schema';
import type { SemanticPage } from '@shared/types';
import type { MessageResponse, TabContext } from '../types/messages';

function hasRuntime(): boolean {
  return typeof chrome !== 'undefined' && Boolean(chrome?.runtime?.sendMessage);
}

async function send<T>(message: unknown): Promise<MessageResponse<T>> {
  if (!hasRuntime()) {
    return { ok: false, error: 'SurfAI is not running inside the extension.' };
  }
  try {
    const response = (await chrome.runtime.sendMessage(message)) as MessageResponse<T>;
    return response ?? { ok: false, error: 'No response from the extension worker.' };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : 'Extension messaging failed.',
    };
  }
}

/** The active tab's URL and title. */
export function getTabContext(): Promise<MessageResponse<TabContext>> {
  return send<TabContext>({ type: 'GET_TAB_CONTEXT' });
}

/** Capture a semantic snapshot of the active tab. */
export function capturePage(maxElements?: number): Promise<MessageResponse<SemanticPage>> {
  return send<SemanticPage>({ type: 'CAPTURE_PAGE', maxElements });
}

/** Execute one validated action in the active tab. */
export function executeAction(
  action: BrowserAction,
  timeoutMs?: number,
): Promise<MessageResponse<ActionResult>> {
  return send<ActionResult>({ type: 'EXECUTE_ACTION', action, timeoutMs });
}

/** Navigate the active tab, used when opening a favourite. */
export async function openUrl(url: string): Promise<MessageResponse<void>> {
  if (!hasRuntime() || !chrome.tabs) {
    return { ok: false, error: 'SurfAI is not running inside the extension.' };
  }
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id) return { ok: false, error: 'No active tab was found.' };
    await chrome.tabs.update(tab.id, { url });
    return { ok: true };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : 'Could not open that page.',
    };
  }
}

/** Resolve once the active tab has finished loading, or after `timeoutMs`. */
export function waitForTabLoad(timeoutMs = 15_000): Promise<boolean> {
  if (!hasRuntime() || !chrome.tabs?.onUpdated) return Promise.resolve(true);

  return new Promise((resolve) => {
    let settled = false;

    const finish = (value: boolean) => {
      if (settled) return;
      settled = true;
      chrome.tabs.onUpdated.removeListener(listener);
      clearTimeout(timer);
      resolve(value);
    };

    const listener = (
      _tabId: number,
      changeInfo: chrome.tabs.TabChangeInfo,
      tab: chrome.tabs.Tab,
    ) => {
      if (changeInfo.status === 'complete' && tab.active) finish(true);
    };

    const timer = setTimeout(() => finish(false), timeoutMs);
    chrome.tabs.onUpdated.addListener(listener);

    // Already idle? Resolve without waiting for an event that will not come.
    chrome.tabs.query({ active: true, currentWindow: true }).then(([tab]) => {
      if (tab?.status === 'complete') finish(true);
    });
  });
}

/** Notified when the user switches tabs or navigates. */
export function onTabChanged(handler: () => void): () => void {
  if (!hasRuntime() || !chrome.tabs) return () => undefined;

  const activated = () => handler();
  const updated = (_id: number, changeInfo: chrome.tabs.TabChangeInfo, tab: chrome.tabs.Tab) => {
    if (tab.active && (changeInfo.status === 'complete' || changeInfo.url)) handler();
  };

  chrome.tabs.onActivated.addListener(activated);
  chrome.tabs.onUpdated.addListener(updated);

  return () => {
    chrome.tabs.onActivated.removeListener(activated);
    chrome.tabs.onUpdated.removeListener(updated);
  };
}
