/**
 * Obtaining a Google token for a hosted backend.
 *
 * Only used when the configured backend requires it. A local backend runs in
 * `local` auth mode and needs no token at all, so signing in is never forced on
 * someone running SurfAI on their own machine.
 *
 * `chrome.identity.getAuthToken` is preferred: Chrome owns the flow, the token
 * is cached by the browser, and no redirect handling is needed. It is only
 * available when the manifest declares an `oauth2` client id, so the absence of
 * one is treated as "this build is local-only" rather than as an error.
 */

const TOKEN_CACHE_KEY = 'surfai.auth.token';

export interface AuthToken {
  token: string;
  /** Epoch milliseconds. Chrome does not tell us, so this is a conservative guess. */
  expiresAt: number;
}

export class AuthError extends Error {
  constructor(
    message: string,
    /** True when the user can fix this by signing in. */
    readonly recoverable = true,
  ) {
    super(message);
    this.name = 'AuthError';
  }
}

/** Does this build have OAuth configured at all? */
export function isAuthConfigured(): boolean {
  if (typeof chrome === 'undefined' || !chrome.runtime?.getManifest) return false;
  const manifest = chrome.runtime.getManifest() as chrome.runtime.Manifest & {
    oauth2?: { client_id?: string };
  };
  return Boolean(manifest.oauth2?.client_id);
}

function hasIdentity(): boolean {
  return typeof chrome !== 'undefined' && Boolean(chrome.identity?.getAuthToken);
}

/**
 * Get a token, prompting the user only when `interactive` is set.
 *
 * Callers pass `interactive: false` on the hot path so a request never pops a
 * sign-in window unexpectedly, and `true` only from an explicit Sign in action.
 */
export function getToken(interactive = false): Promise<string | null> {
  if (!isAuthConfigured() || !hasIdentity()) return Promise.resolve(null);

  return new Promise((resolve) => {
    chrome.identity.getAuthToken({ interactive }, (token) => {
      // Reading lastError is required; leaving it unread logs a warning.
      const error = chrome.runtime.lastError;
      if (error || !token) {
        resolve(null);
        return;
      }
      resolve(typeof token === 'string' ? token : (token as { token: string }).token);
    });
  });
}

/**
 * Discard a token Chrome has cached.
 *
 * Called after a 401: Chrome will happily keep handing back a token the server
 * has already rejected, so it has to be evicted or every retry fails the same
 * way.
 */
export async function invalidateToken(token: string): Promise<void> {
  if (!hasIdentity() || !token) return;
  await new Promise<void>((resolve) => {
    chrome.identity.removeCachedAuthToken({ token }, () => resolve());
  });
  try {
    await chrome.storage?.local?.remove(TOKEN_CACHE_KEY);
  } catch {
    // Storage is a convenience here; failing to clear it is not fatal.
  }
}

/** Sign in explicitly. Shows Google's consent screen if needed. */
export async function signIn(): Promise<string> {
  if (!isAuthConfigured()) {
    throw new AuthError(
      'This build of SurfAI has no OAuth client configured, so it can only talk to a ' +
        'local backend.',
      false,
    );
  }
  const token = await getToken(true);
  if (!token) {
    throw new AuthError('Sign-in was cancelled or failed. Try again.');
  }
  return token;
}

/** Sign out and forget the cached token. */
export async function signOut(): Promise<void> {
  const token = await getToken(false);
  if (token) await invalidateToken(token);
}

/** The signed-in account's email, for display. Null when signed out. */
export async function getAccountEmail(): Promise<string | null> {
  if (!hasIdentity() || !chrome.identity.getProfileUserInfo) return null;
  return new Promise((resolve) => {
    try {
      chrome.identity.getProfileUserInfo((info) => {
        resolve(info?.email || null);
      });
    } catch {
      resolve(null);
    }
  });
}
