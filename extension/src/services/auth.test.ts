/**
 * Google sign-in.
 *
 * The case worth protecting is the confusing one: Chrome refusing to issue a
 * token for a reason the user could act on, reported as a generic failure.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AuthError, getToken, isAuthConfigured, isSignedIn, signIn } from './auth';

type TokenCallback = (token: string | undefined) => void;

function installChrome(options: {
  clientId?: string;
  token?: string;
  lastError?: { message: string };
}) {
  const getAuthToken = vi.fn((_opts: { interactive: boolean }, callback: TokenCallback) => {
    (globalThis as unknown as { chrome: { runtime: { lastError?: unknown } } }).chrome.runtime
      .lastError = options.lastError;
    callback(options.token);
  });

  (globalThis as unknown as { chrome: unknown }).chrome = {
    runtime: {
      getManifest: () =>
        options.clientId ? { oauth2: { client_id: options.clientId } } : {},
      lastError: undefined,
    },
    identity: { getAuthToken, removeCachedAuthToken: vi.fn((_a, cb: () => void) => cb()) },
    storage: { local: { remove: vi.fn(async () => undefined) } },
  };
  return { getAuthToken };
}

beforeEach(() => {
  delete (globalThis as unknown as { chrome?: unknown }).chrome;
});

describe('isAuthConfigured', () => {
  it('is false for a build with no OAuth client', () => {
    installChrome({});
    expect(isAuthConfigured()).toBe(false);
  });

  it('is false outside the extension, so unit tests never touch identity', () => {
    expect(isAuthConfigured()).toBe(false);
  });
});

describe('getToken', () => {
  it('returns the token Chrome issues', async () => {
    installChrome({ clientId: 'abc.apps.googleusercontent.com', token: 'ya29.token' });
    expect(await getToken(false)).toBe('ya29.token');
  });

  it('never prompts on the hot path', async () => {
    const { getAuthToken } = installChrome({
      clientId: 'abc.apps.googleusercontent.com',
      token: 'ya29.token',
    });
    await getToken();
    expect(getAuthToken.mock.calls[0][0]).toEqual({ interactive: false });
  });

  it('resolves to null rather than throwing when there is no token', async () => {
    // A signed-out user hits this on every request; it must not be an error.
    installChrome({
      clientId: 'abc.apps.googleusercontent.com',
      lastError: { message: 'OAuth2 not granted or revoked.' },
    });
    expect(await getToken(false)).toBeNull();
    expect(await isSignedIn()).toBe(false);
  });
});

describe('signIn', () => {
  it("reports Chrome's reason for refusing", async () => {
    // The one that actually happens: an OAuth client registered as a Web
    // application instead of a Chrome Extension. "Sign-in failed" leaves
    // nothing to act on; this names the problem.
    installChrome({
      clientId: 'abc.apps.googleusercontent.com',
      lastError: { message: "Service responded with error: 'bad client id'" },
    });

    await expect(signIn()).rejects.toThrow(/bad client id/);
  });

  it('explains that a build without an OAuth client is local-only', async () => {
    installChrome({});
    await expect(signIn()).rejects.toBeInstanceOf(AuthError);
    await expect(signIn()).rejects.toThrow(/local backend/);
  });

  it('prompts, unlike the hot path', async () => {
    const { getAuthToken } = installChrome({
      clientId: 'abc.apps.googleusercontent.com',
      token: 'ya29.token',
    });
    expect(await signIn()).toBe('ya29.token');
    expect(getAuthToken.mock.calls[0][0]).toEqual({ interactive: true });
  });
});
