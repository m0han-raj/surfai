/**
 * Chrome storage wrapper.
 *
 * Only preferences and lightweight UI state live here. Page content, extracted
 * data and credentials never touch extension storage -- favourites and history
 * belong to the backend, and nothing sensitive is cached locally.
 */

import { DEFAULT_BACKEND_URL } from './constants';

export interface SurfAISettings {
  backendUrl: string;
  /** Cap on elements captured per snapshot; lower is cheaper and faster. */
  maxElements: number;
  /**
   * How much of a page's text SurfAI reads when you ask about it.
   *
   * The difference between answering about a page and answering about its
   * buttons. Higher is better until it meets the model provider's per-minute
   * token limit, which is why it is a setting and not a constant.
   */
  maxTextChars: number;
  actionTimeoutMs: number;
  /** Auto-run low-risk actions. Medium and high always confirm regardless. */
  autoRunLowRisk: boolean;
  /**
   * Drive a browser through Playwright MCP instead of your own tab.
   *
   * Explicit rather than automatic, because the two act on different
   * browsers: the normal path acts on the tab in front of you, this one on
   * whatever browser the MCP server was pointed at.
   */
  browserControl: boolean;
  theme: 'light' | 'dark' | 'system';
}

export const DEFAULT_SETTINGS: SurfAISettings = {
  backendUrl: DEFAULT_BACKEND_URL,
  maxElements: 60,
  maxTextChars: 12_000,
  actionTimeoutMs: 10_000,
  autoRunLowRisk: true,
  browserControl: false,
  theme: 'light',
};

const SETTINGS_KEY = 'surfai.settings';
const DRAFT_KEY = 'surfai.draft';
// Set once the backend address has been probed for, so a later probe never
// second-guesses an address the user has since chosen by hand.
const DISCOVERY_KEY = 'surfai.backend-discovered';

/** Available only inside the extension; guarded so unit tests can run in jsdom. */
function hasChromeStorage(): boolean {
  return typeof chrome !== 'undefined' && Boolean(chrome?.storage?.local);
}

const memoryStore = new Map<string, unknown>();

async function readKey<T>(key: string, fallback: T): Promise<T> {
  if (!hasChromeStorage()) {
    return (memoryStore.get(key) as T) ?? fallback;
  }
  try {
    const result = await chrome.storage.local.get(key);
    return (result?.[key] as T) ?? fallback;
  } catch {
    return fallback;
  }
}

async function writeKey(key: string, value: unknown): Promise<void> {
  if (!hasChromeStorage()) {
    memoryStore.set(key, value);
    return;
  }
  try {
    await chrome.storage.local.set({ [key]: value });
  } catch (error) {
    console.warn('[SurfAI] Could not write to storage', error);
  }
}

export async function getSettings(): Promise<SurfAISettings> {
  const stored = await readKey<Partial<SurfAISettings>>(SETTINGS_KEY, {});
  // Merge so a setting added in a later version gets its default.
  return { ...DEFAULT_SETTINGS, ...stored };
}

export async function saveSettings(patch: Partial<SurfAISettings>): Promise<SurfAISettings> {
  const next = { ...(await getSettings()), ...patch };
  await writeKey(SETTINGS_KEY, next);
  return next;
}

export async function resetSettings(): Promise<SurfAISettings> {
  await writeKey(SETTINGS_KEY, DEFAULT_SETTINGS);
  return DEFAULT_SETTINGS;
}

/** Has the one-time search for a local backend already run? */
export async function hasSearchedForBackend(): Promise<boolean> {
  return readKey<boolean>(DISCOVERY_KEY, false);
}

export async function markBackendSearched(): Promise<void> {
  await writeKey(DISCOVERY_KEY, true);
}

/** Preserve an unsent message across panel close/reopen. */
export async function getDraft(): Promise<string> {
  return readKey<string>(DRAFT_KEY, '');
}

export async function saveDraft(text: string): Promise<void> {
  await writeKey(DRAFT_KEY, text.slice(0, 4000));
}

export function onSettingsChanged(handler: (settings: SurfAISettings) => void): () => void {
  if (!hasChromeStorage() || !chrome.storage.onChanged) {
    return () => undefined;
  }
  const listener = (
    changes: Record<string, chrome.storage.StorageChange>,
    area: string,
  ) => {
    if (area === 'local' && changes[SETTINGS_KEY]) {
      handler({ ...DEFAULT_SETTINGS, ...(changes[SETTINGS_KEY].newValue as SurfAISettings) });
    }
  };
  chrome.storage.onChanged.addListener(listener);
  return () => chrome.storage.onChanged.removeListener(listener);
}

/** Test helper. */
export function __clearMemoryStore(): void {
  memoryStore.clear();
}
