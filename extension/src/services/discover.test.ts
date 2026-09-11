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
import { __clearMemoryStore, getSettings, markBackendSearched, saveSettings } from './storage';

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

  it('reconsiders an install left stuck on the default by the old logic', async () => {
    // An earlier version recorded the search as done even when it found
    // nothing, which pinned those installs to the default address. Under the
    // current logic that combination cannot arise: a completed search either
    // found a backend or the user had configured one, and neither leaves the
    // default in place. So the pair is the old bug's fingerprint, and seeing
    // it means the search never really happened.
    await saveSettings({ backendUrl: DEFAULT_BACKEND_URL });
    await markBackendSearched();

    const fetchMock = fetchServing('http://localhost:8010');
    vi.stubGlobal('fetch', fetchMock);

    expect(await ensureBackendUrl()).toBe('http://localhost:8010');
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

  it('tries again later when a search found nothing', async () => {
    // This originally asserted the opposite, and the opposite was wrong.
    //
    // A search can fail for reasons that are temporary and invisible from
    // here: the backend is not started yet, or Chrome is blocking the probe
    // because host access for localhost has been switched off. Recording the
    // search as done in that case pins the panel to the default address
    // forever, and the default is a popular port that some other application
    // is quite likely answering. That is exactly what happened in the wild:
    // probes blocked, fell back to :8000, another server answered there, and
    // no later run ever reconsidered it.
    //
    // Finding nothing is not an answer, so it is not recorded as one.
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch'); }));
    expect(await ensureBackendUrl()).toBe(DEFAULT_BACKEND_URL);

    // Later: the backend is up, or access was granted.
    const fetchMock = fetchServing('http://localhost:8010');
    vi.stubGlobal('fetch', fetchMock);

    expect(await ensureBackendUrl()).toBe('http://localhost:8010');
    expect((await getSettings()).backendUrl).toBe('http://localhost:8010');
  });

  it('does not repeat the search within one panel session', async () => {
    // The retry above must not turn into a probe on every request. Callers
    // hold the in-flight promise for the session; this pins the storage half
    // of that contract: a successful search is recorded and never repeated.
    const fetchMock = fetchServing('http://localhost:8010');
    vi.stubGlobal('fetch', fetchMock);
    await ensureBackendUrl();

    const after = fetchMock.mock.calls.length;
    await ensureBackendUrl();
    expect(fetchMock.mock.calls.length).toBe(after);
  });
});
