/**
 * Values shared between the storage layer and the HTTP client.
 *
 * `api.ts` imports `storage.ts` to read the configured backend address, so the
 * default cannot live in either without the two importing each other.
 */

/** Where the backend is assumed to be until something says otherwise. */
export const DEFAULT_BACKEND_URL = 'http://localhost:8000';
