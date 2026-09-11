/**
 * What the panel says when a request fails.
 *
 * These messages are the only diagnostic a user ever gets, and the first real
 * failure proved they were not enough: "The backend returned 404." with no
 * address, while three candidate backends were running on this machine and
 * none of their logs showed the request. Naming the address turns that into a
 * one-glance answer.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError, api } from './api';
import { __clearMemoryStore, saveSettings } from './storage';

function respondWith(init: { status: number; body?: unknown }) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({
      ok: init.status >= 200 && init.status < 300,
      status: init.status,
      text: async () => (init.body === undefined ? '' : JSON.stringify(init.body)),
      json: async () => init.body,
    })),
  );
}

beforeEach(async () => {
  __clearMemoryStore();
  delete (globalThis as unknown as { chrome?: unknown }).chrome;
  vi.restoreAllMocks();
  // Pin the address so discovery never runs and the tests stay hermetic.
  await saveSettings({ backendUrl: 'http://localhost:8000' });
});

describe('request failures', () => {
  it('names the address that answered when the path is not found', async () => {
    respondWith({ status: 404 });

    await expect(api.health()).rejects.toThrow(/http:\/\/localhost:8000/);
  });

  it('suggests the obvious cause of a 404 from a live server', async () => {
    // A 404 means something answered, just not SurfAI. That is a different
    // problem from "the backend is down" and needs a different fix.
    respondWith({ status: 404 });

    await expect(api.health()).rejects.toThrow(/not a SurfAI backend|different application/i);
  });

  it('names the address when nothing answers at all', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch'); }));

    await expect(api.health()).rejects.toThrow(/http:\/\/localhost:8000/);
  });

  it('names the address when the backend is too slow', async () => {
    // Fake timers so the assertion does not actually wait out the request
    // timeout, which is the same length as the test runner's own deadline.
    vi.useFakeTimers();
    vi.stubGlobal(
      'fetch',
      vi.fn((_u: string, init: RequestInit) =>
        new Promise((_res, rej) => {
          init.signal?.addEventListener('abort', () =>
            rej(new DOMException('aborted', 'AbortError')),
          );
        }),
      ),
    );

    try {
      const pending = api.health();
      const assertion = expect(pending).rejects.toThrow(/http:\/\/localhost:8000/);
      await vi.advanceTimersByTimeAsync(6000);
      await assertion;
    } finally {
      vi.useRealTimers();
    }
  });

  it('prefers the backend’s own explanation when it gives one', async () => {
    // Our 422 for a malformed page snapshot names the offending field; that is
    // far more useful than anything this layer could invent.
    respondWith({
      status: 422,
      body: { detail: 'Invalid page snapshot: elements.0.type - Field required' },
    });

    await expect(api.health()).rejects.toThrow(/elements\.0\.type/);
  });

  it('carries the status code for callers that branch on it', async () => {
    respondWith({ status: 404 });

    await expect(api.health()).rejects.toMatchObject({ status: 404 });
    await expect(api.health()).rejects.toBeInstanceOf(ApiError);
  });

  it('refuses a /health answer that is not from SurfAI', async () => {
    // This is what crashed the panel. Another application on the configured
    // port answered `{"status":"healthy"}`, the panel took it for its own
    // backend, and reading `health.database.missing_tables` threw
    // "Cannot read properties of undefined". The shape is not ours, so the
    // fix is to refuse it here rather than to guard every field that reads it.
    respondWith({ status: 200, body: { status: 'healthy' } });

    await expect(api.health()).rejects.toThrow(/not a SurfAI backend|not SurfAI/i);
  });

  it('names the address when the wrong application answers', async () => {
    respondWith({ status: 200, body: { status: 'healthy' } });

    await expect(api.health()).rejects.toThrow(/http:\/\/localhost:8000/);
  });

  it('reports that as a reachability problem, not a crash', async () => {
    respondWith({ status: 200, body: { status: 'healthy' } });

    // The Settings panel renders a reachable/unreachable row from this; the
    // wrong application on the port belongs in the unreachable column.
    await expect(api.health()).rejects.toMatchObject({ isNetwork: true });
  });

  it('still succeeds when the backend is healthy', async () => {
    respondWith({ status: 200, body: { status: 'ok', app: 'SurfAI' } });

    await expect(api.health()).resolves.toMatchObject({ app: 'SurfAI' });
  });
});
