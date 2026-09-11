/**
 * Backend discovery.
 *
 * The risk is not "failed to find the backend" -- that just leaves the default
 * in place and the existing unreachable-backend message. The risk is
 * discovery overriding an address the user chose deliberately, so most of
 * these check what it declines to do.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { discoverBackend, ensureBackendUrl, probe } from './discover';
import { DEFAULT_BACKEND_URL } from './constants';
import { __clearMemoryStore, getSettings, saveSettings } from './storage';

/** A fetch that answers like SurfAI on the given origins and refuses elsewhere. */
function fetchServing(...healthy: string[]) {
  return vi.fn(async (url: string) => {
    const origin = String(url).replace(/\/health$/, '');
    if (!healthy.includes(origin)) throw new TypeError('Failed to fetch');
    return {
      ok: true,
      json: async () => ({ status: 'ok', app: 'SurfAI' }),
    } as unknown as Response;
  });
}

beforeEach(() => {
  __clearMemoryStore();
  delete (globalThis as unknown as { chrome?: unknown }).chrome;
  vi.restoreAllMocks();
});

describe('probe', () => {
  it('accepts a backend that identifies itself', async () => {
    vi.stubGlobal('fetch', fetchServing('http://localhost:8010'));
    expect(await probe('http://localhost:8010')).toBe(true);
  });

  it('rejects an unrelated server on the same port', async () => {
    // Port 8000 is popular. Answering 200 is not evidence of anything.
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: true, json: async () => ({ app: 'something-else' }) })),
    );
    expect(await probe('http://localhost:8000')).toBe(false);
  });

  it('rejects a server that answers with an error status', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, json: async () => ({}) })));
    expect(await probe('http://localhost:8000')).toBe(false);
  });

  it('treats a connection refusal as absence rather than failing', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch'); }));
    expect(await probe('http://localhost:8000')).toBe(false);
  });

  it('gives up rather than hanging on a port that never answers', async () => {
    // A filtered port accepts the connection and then says nothing, which is
    // what would otherwise block the panel's first request indefinitely.
    vi.stubGlobal(
      'fetch',
      vi.fn(
        (_url: string, init: RequestInit) =>
          new Promise((_resolve, reject) => {
            init.signal?.addEventListener('abort', () =>
              reject(new DOMException('aborted', 'AbortError')),
            );
          }),
      ),
    );
    expect(await probe('http://localhost:8000', 10)).toBe(false);
  });
});

describe('discoverBackend', () => {
  it('finds a backend on a non-default port', async () => {
    vi.stubGlobal('fetch', fetchServing('http://localhost:8010'));
    expect(await discoverBackend()).toBe('http://localhost:8010');
  });

  it('prefers the earlier candidate when several answer', async () => {
    vi.stubGlobal('fetch', fetchServing('http://localhost:8000', 'http://localhost:8010'));
    expect(await discoverBackend()).toBe('http://localhost:8000');
  });

  it('falls back to the default when nothing answers', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch'); }));
    expect(await discoverBackend()).toBe(DEFAULT_BACKEND_URL);
  });
});

describe('ensureBackendUrl', () => {
  it('saves what it finds on first run', async () => {
    vi.stubGlobal('fetch', fetchServing('http://localhost:8010'));

    expect(await ensureBackendUrl()).toBe('http://localhost:8010');
    expect((await getSettings()).backendUrl).toBe('http://localhost:8010');
  });

  it('never probes again once it has run', async () => {
    const fetchMock = fetchServing('http://localhost:8010');
    vi.stubGlobal('fetch', fetchMock);
    await ensureBackendUrl();

    const callsAfterFirstRun = fetchMock.mock.calls.length;
    await ensureBackendUrl();
    expect(fetchMock.mock.calls.length).toBe(callsAfterFirstRun);
  });

  it('leaves an address the user configured alone', async () => {
    // The whole point: a probe finding a live local backend must not move
    // someone off the hosted backend they deliberately pointed at.
    await saveSettings({ backendUrl: 'https://surfai.example.com' });
    const fetchMock = fetchServing('http://localhost:8010');
    vi.stubGlobal('fetch', fetchMock);

    expect(await ensureBackendUrl()).toBe('https://surfai.example.com');
    expect(fetchMock).not.toHaveBeenCalled();
    expect((await getSettings()).backendUrl).toBe('https://surfai.example.com');
  });

  it('does not retry after a search that found nothing', async () => {
    // Otherwise every panel open pays the probe cost for a backend that is
    // simply not running locally.
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch'); }));
    expect(await ensureBackendUrl()).toBe(DEFAULT_BACKEND_URL);

    const fetchMock = fetchServing('http://localhost:8010');
    vi.stubGlobal('fetch', fetchMock);
    expect(await ensureBackendUrl()).toBe(DEFAULT_BACKEND_URL);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
