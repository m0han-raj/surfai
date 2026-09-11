/**
 * Settings storage.
 *
 * Verifies the merge-with-defaults behaviour (so a setting added in a later
 * version does not read as undefined) and that storage failures degrade rather
 * than throw -- an extension must stay usable when storage is unavailable.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  DEFAULT_SETTINGS,
  __clearMemoryStore,
  getDraft,
  getSettings,
  resetSettings,
  saveDraft,
  saveSettings,
} from './storage';

type StorageShape = Record<string, unknown>;

function installChromeStorage(initial: StorageShape = {}) {
  const store: StorageShape = { ...initial };
  const local = {
    get: vi.fn(async (key: string) => ({ [key]: store[key] })),
    set: vi.fn(async (items: StorageShape) => {
      Object.assign(store, items);
    }),
  };
  (globalThis as unknown as { chrome: unknown }).chrome = {
    storage: { local, onChanged: { addListener: vi.fn(), removeListener: vi.fn() } },
  };
  return { store, local };
}

describe('settings storage', () => {
  beforeEach(() => {
    __clearMemoryStore();
    delete (globalThis as unknown as { chrome?: unknown }).chrome;
  });

  it('returns defaults when nothing is stored', async () => {
    installChromeStorage();
    expect(await getSettings()).toEqual(DEFAULT_SETTINGS);
  });

  it('round-trips a saved setting', async () => {
    installChromeStorage();
    await saveSettings({ backendUrl: 'http://127.0.0.1:9000' });
    expect((await getSettings()).backendUrl).toBe('http://127.0.0.1:9000');
  });

  it('fills in defaults for keys added in a later version', async () => {
    // An older install stored only one key.
    installChromeStorage({ 'surfai.settings': { backendUrl: 'http://old:8000' } });

    const settings = await getSettings();
    expect(settings.backendUrl).toBe('http://old:8000');
    expect(settings.maxElements).toBe(DEFAULT_SETTINGS.maxElements);
    expect(settings.autoRunLowRisk).toBe(DEFAULT_SETTINGS.autoRunLowRisk);
  });

  it('resets to defaults', async () => {
    installChromeStorage();
    await saveSettings({ maxElements: 150 });
    expect(await resetSettings()).toEqual(DEFAULT_SETTINGS);
    expect((await getSettings()).maxElements).toBe(DEFAULT_SETTINGS.maxElements);
  });

  it('falls back to defaults when storage throws', async () => {
    (globalThis as unknown as { chrome: unknown }).chrome = {
      storage: {
        local: {
          get: vi.fn(async () => {
            throw new Error('storage unavailable');
          }),
          set: vi.fn(async () => {
            throw new Error('storage unavailable');
          }),
        },
      },
    };

    expect(await getSettings()).toEqual(DEFAULT_SETTINGS);
    // Writing must not reject either.
    await expect(saveSettings({ maxElements: 30 })).resolves.toBeTruthy();
  });

  it('works outside the extension, for unit tests and dev', async () => {
    await saveSettings({ theme: 'dark' });
    expect((await getSettings()).theme).toBe('dark');
  });
});

describe('draft persistence', () => {
  beforeEach(() => {
    __clearMemoryStore();
    delete (globalThis as unknown as { chrome?: unknown }).chrome;
  });

  it('keeps an unsent message', async () => {
    installChromeStorage();
    await saveDraft('find laptops under 80000');
    expect(await getDraft()).toBe('find laptops under 80000');
  });

  it('returns an empty string when there is no draft', async () => {
    installChromeStorage();
    expect(await getDraft()).toBe('');
  });

  it('caps an oversized draft', async () => {
    installChromeStorage();
    await saveDraft('x'.repeat(9000));
    expect((await getDraft()).length).toBe(4000);
  });
});
