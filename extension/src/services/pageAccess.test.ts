/**
 * The one-click grant that lets SurfAI follow you between tabs.
 *
 * `activeTab` covers the tab the panel was opened on and nothing you navigate
 * to afterwards, which is why the chat kept not knowing which site you were
 * looking at. Broad access is the only thing that fixes that, so it is asked
 * for explicitly, at a moment the user chose, rather than taken at install.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { hasPageAccess, requestPageAccess, revokePageAccess } from './pageAccess';

function installPermissions(granted: boolean, outcome = true) {
  const contains = vi.fn(async () => granted);
  const request = vi.fn(async () => outcome);
  const remove = vi.fn(async () => true);
  (globalThis as unknown as { chrome: unknown }).chrome = {
    permissions: { contains, request, remove },
  };
  return { contains, request, remove };
}

beforeEach(() => {
  delete (globalThis as unknown as { chrome?: unknown }).chrome;
});

describe('hasPageAccess', () => {
  it('is true once broad access has been granted', async () => {
    installPermissions(true);
    expect(await hasPageAccess()).toBe(true);
  });

  it('is false before it has', async () => {
    installPermissions(false);
    expect(await hasPageAccess()).toBe(false);
  });

  it('asks about every site, which is what following tabs requires', async () => {
    const { contains } = installPermissions(true);
    await hasPageAccess();
    expect(contains).toHaveBeenCalledWith({ origins: ['<all_urls>'] });
  });

  it('is false outside the extension rather than throwing', async () => {
    // Unit tests and any non-extension context land here.
    expect(await hasPageAccess()).toBe(false);
  });

  it('is false when the call itself fails', async () => {
    (globalThis as unknown as { chrome: unknown }).chrome = {
      permissions: { contains: vi.fn(async () => { throw new Error('nope'); }) },
    };
    expect(await hasPageAccess()).toBe(false);
  });
});

describe('requestPageAccess', () => {
  it('reports that the user accepted', async () => {
    installPermissions(false, true);
    expect(await requestPageAccess()).toBe(true);
  });

  it('reports that the user declined, without treating it as an error', async () => {
    // Declining is a legitimate answer, not a failure. SurfAI keeps working
    // on the tab it was opened on.
    installPermissions(false, false);
    expect(await requestPageAccess()).toBe(false);
  });

  it('prompts synchronously, because the user gesture does not survive an await', () => {
    // This is the whole ballgame. Chrome only honours permissions.request()
    // while the click that triggered it is still on the stack, and a single
    // await beforehand ends that. The symptom is silent: the button depresses,
    // no prompt appears, nothing throws, and nothing is logged.
    //
    // An earlier version of this file asked "is it already granted?" first,
    // out of a worry about a prompt flashing for no reason, and bought that
    // cosmetic point at the cost of the feature working at all. Deliberately
    // not awaited here: the call must already have happened.
    const { request } = installPermissions(false);

    void requestPageAccess();

    expect(request).toHaveBeenCalledWith({ origins: ['<all_urls>'] });
  });

  it('leaves the already-granted case to the caller', () => {
    // The panel renders a different button in that state, so it never asks.
    // Checking here would mean an await, and an await means no prompt.
    const { request } = installPermissions(true);

    void requestPageAccess();

    expect(request).toHaveBeenCalled();
  });

  it('returns false rather than throwing when the prompt errors', async () => {
    // chrome.permissions.request rejects outright when called without a user
    // gesture, and that must not take the panel down.
    (globalThis as unknown as { chrome: unknown }).chrome = {
      permissions: {
        contains: vi.fn(async () => false),
        request: vi.fn(async () => { throw new Error('user gesture required'); }),
      },
    };
    expect(await requestPageAccess()).toBe(false);
  });
});

describe('revokePageAccess', () => {
  it('gives the access back', async () => {
    const { remove } = installPermissions(true);
    await revokePageAccess();
    expect(remove).toHaveBeenCalledWith({ origins: ['<all_urls>'] });
  });

  it('is harmless when there is nothing to revoke', async () => {
    await expect(revokePageAccess()).resolves.not.toThrow();
  });
});
