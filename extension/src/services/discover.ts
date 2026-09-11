/**
 * Finding the backend on first run.
 *
 * The panel used to default to `localhost:8000` and simply fail if the backend
 * was anywhere else, with a network error that looks identical to "the backend
 * is not running". Port 8000 is a popular port; colliding with something else
 * and having to discover the Settings field is a poor first five minutes.
 *
 * So on first run only, the candidates below are probed and the first healthy
 * one is saved. After that the saved value is authoritative and is never
 * second-guessed: an explicit choice in Settings must not be overridden by a
 * probe that happens to find something else.
 */

import { DEFAULT_BACKEND_URL } from './constants';
import {
  getSettings,
  hasSearchedForBackend,
  markBackendSearched,
  saveSettings,
} from './storage';

/**
 * Where a locally-run backend plausibly lives, best first.
 *
 * Only localhost: probing anything else would mean the extension quietly
 * contacting a host the user never named.
 */
export const LOCAL_CANDIDATES = [
  'http://localhost:8000',
  'http://127.0.0.1:8000',
  'http://localhost:8010',
  'http://localhost:8001',
] as const;

const PROBE_TIMEOUT_MS = 1200;

/** Does a SurfAI backend answer here? */
export async function probe(baseUrl: string, timeoutMs = PROBE_TIMEOUT_MS): Promise<boolean> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${baseUrl.replace(/\/+$/, '')}/health`, {
      signal: controller.signal,
    });
    if (!response.ok) return false;

    // Something else may well be listening on 8000. Only accept a response
    // that is recognisably this application.
    const body = (await response.json()) as { app?: string };
    return body?.app === 'SurfAI';
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * The first candidate that answers, or the default if none do.
 *
 * Probes run in sequence rather than in parallel so the preferred candidate
 * wins when several are up, which matters if a stale backend is still running
 * on another port.
 */
export async function discoverBackend(
  candidates: readonly string[] = LOCAL_CANDIDATES,
): Promise<string> {
  for (const candidate of candidates) {
    if (await probe(candidate)) return candidate;
  }
  return DEFAULT_BACKEND_URL;
}

/**
 * Point the panel at a backend, once, on first run.
 *
 * Returns the address in effect afterwards. Safe to call on every panel open:
 * after the first call it reads one storage key and returns.
 */
export async function ensureBackendUrl(
  candidates: readonly string[] = LOCAL_CANDIDATES,
): Promise<string> {
  const settings = await getSettings();
  if (await hasSearchedForBackend()) {
    // One exception, for installs an earlier version left stuck. It recorded
    // the search as done even when it found nothing, pinning the panel to the
    // default address; since the default is a popular port, that often meant
    // pinned to another application's server. Under the logic below the pair
    // "search completed" and "still on the default" cannot occur, so finding
    // it means the search never really produced an answer. Search again.
    if (settings.backendUrl === DEFAULT_BACKEND_URL) {
      return searchAndSave(candidates);
    }
    return settings.backendUrl;
  }

  if (settings.backendUrl !== DEFAULT_BACKEND_URL) {
    // Already configured, by hand, by an earlier version, or by policy. That
    // is an answer, so the search is over.
    await markBackendSearched();
    return settings.backendUrl;
  }

  return searchAndSave(candidates);
}

/** Probe, and record the result only if it is one. */
async function searchAndSave(candidates: readonly string[]): Promise<string> {
  const found = await discoverBackend(candidates);
  if (found === DEFAULT_BACKEND_URL) {
    // Found nothing. Deliberately not recorded as a completed search: the
    // causes are usually temporary and invisible from here, such as a backend
    // that has not started yet or host access for localhost being switched
    // off in Chrome, and recording it would pin the panel to the default
    // address for good. The default is a popular port, so "pinned to the
    // default" often means pinned to somebody else's server.
    //
    // Repeating it on every request is prevented by the caller holding the
    // in-flight promise for the session, not by this flag.
    return found;
  }

  await saveSettings({ backendUrl: found });
  await markBackendSearched();
  return found;
}
