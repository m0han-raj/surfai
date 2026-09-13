/**
 * Backend HTTP client.
 *
 * The extension holds no model credentials: every LLM call goes through the
 * backend, which reads its key from the environment. That is why the panel
 * talks to a backend rather than to an inference endpoint directly.
 *
 * A hosted backend additionally requires a Google bearer token, attached here
 * and refreshed once on a 401. A local backend requires none, so nothing here
 * prompts a local user to sign in.
 */

import type { Directive } from '../types/agent';
import type { Favourite, FavouriteDraft } from '../types/favourites';
import type { Task } from '@shared/types';
import type { ActionResult } from '@shared/action-schema';
import type { SemanticPage } from '@shared/types';
import type { TabContext } from '../types/messages';
import { getSettings } from './storage';
import { getToken, invalidateToken, isAuthConfigured } from './auth';
import { DEFAULT_BACKEND_URL } from './constants';
import { ensureBackendUrl } from './discover';

export { DEFAULT_BACKEND_URL };

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number = 0,
    readonly isNetwork = false,
    /** The caller should prompt the user to sign in. */
    readonly needsSignIn = false,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

// Kicked off by the first request of the panel session and awaited by all of
// them, so concurrent callers do not each start their own search.
let firstRun: Promise<unknown> | null = null;

async function baseUrl(): Promise<string> {
  // A failed search must not fail the request; it just leaves the default in
  // place, and the request then reports the backend as unreachable as before.
  firstRun ??= ensureBackendUrl().catch(() => undefined);
  await firstRun;

  // Read settings after the search rather than reusing its result, so an
  // address the user edits mid-session takes effect on the very next request.
  const settings = await getSettings();
  return (settings.backendUrl || DEFAULT_BACKEND_URL).replace(/\/+$/, '');
}

async function send(
  url: string,
  init: RequestInit,
  timeoutMs: number,
  token: string | null,
): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  try {
    return await fetch(url, {
      ...init,
      signal: controller.signal,
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(init.headers ?? {}),
      },
    });
  } finally {
    clearTimeout(timer);
  }
}

async function request<T>(
  path: string,
  options: RequestInit & { timeoutMs?: number } = {},
): Promise<T> {
  const { timeoutMs = 180_000, ...init } = options;
  const base = await baseUrl();
  const url = `${base}${path}`;

  // A local backend runs unauthenticated, so no token is fetched and no
  // sign-in is ever forced on someone running SurfAI on their own machine.
  let token = isAuthConfigured() ? await getToken(false) : null;

  let response: Response;
  try {
    response = await send(url, init, timeoutMs, token);

    // Chrome caches tokens and will keep returning one the server has already
    // rejected, so a 401 has to evict it before retrying.
    if (response.status === 401 && token) {
      await invalidateToken(token);
      token = await getToken(false);
      if (token) {
        response = await send(url, init, timeoutMs, token);
      }
    }
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new ApiError(`The backend at ${base} took too long to respond.`, 0, true);
    }
    throw new ApiError(
      `Could not reach a SurfAI backend at ${base}. Check that it is running, ` +
        'and that the address in Settings is correct.',
      0,
      true,
    );
  }

  if (response.status === 401) {
    throw new ApiError(
      'This backend requires you to sign in. Open Settings and sign in with Google.',
      401,
      false,
      true,
    );
  }

  if (response.status === 204) {
    return undefined as T;
  }

  const text = await response.text();
  let body: unknown;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = null;
  }

  if (!response.ok) {
    // The backend's own explanation is always better than anything invented
    // here: a 422 for a bad page snapshot names the offending field.
    const detail = (body as { detail?: string })?.detail ?? describeStatus(response.status, base);
    throw new ApiError(detail, response.status);
  }

  return body as T;
}

/**
 * A failure with no body, said in terms of what to do about it.
 *
 * Always names the address. The first real failure in the wild read "The
 * backend returned 404." while three different servers were listening on this
 * machine and none of their logs showed the request, which left nothing to act
 * on. A 404 in particular means something answered and it was not SurfAI, a
 * different problem from the backend being down and a different fix.
 */
function describeStatus(status: number, base: string): string {
  if (status === 404) {
    return (
      `${base} answered, but it is not a SurfAI backend (404 on the API path). ` +
      'Another application is probably using that port. Check the address in Settings.'
    );
  }
  if (status >= 500) {
    return `The SurfAI backend at ${base} hit an error (${status}). Check its log.`;
  }
  return `The backend at ${base} returned ${status}.`;
}

// --- health ---------------------------------------------------------------

export interface ConversationSummary {
  id: string;
  title: string;
  message_count: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface StoredMessage {
  id: string;
  role: 'user' | 'assistant' | 'notice';
  content: string;
  page_url: string | null;
  warnings: string[];
  created_at: string | null;
}

export interface StoredConversation {
  id: string;
  title: string;
  created_at: string | null;
  updated_at: string | null;
  messages: StoredMessage[];
}

export interface HealthResponse {
  status: string;
  app: string;
  environment: string;
  database: { connected: boolean; error: string | null; missing_tables?: string[] };
  auth_provider: string;
  agent: { max_steps: number; max_retries: number; action_timeout_ms: number };
}

export interface LlmHealthResponse {
  status: string;
  base_url: string;
  model: string;
  api_key_configured: boolean;
  reachable: boolean;
  model_available: boolean | null;
  available_models?: string[];
  error: string | null;
}

export const api = {
  health: async () => {
    const body = await request<HealthResponse>('/health', { method: 'GET', timeoutMs: 5000 });
    // Port collisions are the normal case, not an exotic one, and plenty of
    // servers answer `/health` with a 200. Taking one of those for our own
    // backend is what crashed the panel: every field below `database` came
    // back undefined and the first read of one threw. Checking the identity
    // here beats guarding each of its readers, and it is the same check
    // discovery already applies before adopting an address.
    if (body?.app !== 'SurfAI') {
      throw new ApiError(
        `Something is answering at ${await baseUrl()}, but it is not a SurfAI backend. ` +
          'Another application is probably using that port. Check the address in Settings.',
        0,
        true,
      );
    }
    return body;
  },

  llmHealth: () => request<LlmHealthResponse>('/health/llm', { method: 'GET', timeoutMs: 15000 }),

  // --- chat and tasks -----------------------------------------------------

  chat: (payload: {
    message: string;
    page_context: SemanticPage | Record<string, unknown>;
    tab_context: TabContext;
    history?: Array<{ role: 'user' | 'assistant'; content: string }>;
    conversation_id?: string | null;
    browser_control?: boolean;
  }) =>
    request<Directive>('/api/chat', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  createTask: (payload: {
    request: string;
    page_context: SemanticPage | Record<string, unknown>;
    tab_context: TabContext;
    favourite_id?: string;
  }) =>
    request<Directive>('/api/tasks', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  continueTask: (
    taskId: string,
    payload: {
      page_context: SemanticPage | Record<string, unknown>;
      result?: ActionResult | null;
      confirmation?: boolean | null;
    },
  ) =>
    request<Directive>(`/api/tasks/${encodeURIComponent(taskId)}/continue`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  cancelTask: (taskId: string) =>
    request<Directive>(`/api/tasks/${encodeURIComponent(taskId)}/cancel`, {
      method: 'POST',
      timeoutMs: 10_000,
    }),

  listTasks: (limit = 50) =>
    request<{ tasks: Task[]; total: number }>(`/api/tasks?limit=${limit}`, { method: 'GET' }),

  getTask: (taskId: string) =>
    request<Task>(`/api/tasks/${encodeURIComponent(taskId)}`, { method: 'GET' }),

  deleteTask: (taskId: string) =>
    request<void>(`/api/tasks/${encodeURIComponent(taskId)}`, { method: 'DELETE' }),

  // --- conversations ------------------------------------------------------

  listConversations: (limit = 50) =>
    request<{ conversations: ConversationSummary[]; total: number }>(
      `/api/conversations?limit=${limit}`,
      { method: 'GET' },
    ),

  getConversation: (id: string) =>
    request<StoredConversation>(`/api/conversations/${encodeURIComponent(id)}`, {
      method: 'GET',
    }),

  deleteConversation: (id: string) =>
    request<void>(`/api/conversations/${encodeURIComponent(id)}`, { method: 'DELETE' }),

  // --- favourites ---------------------------------------------------------

  listFavourites: () => request<Favourite[]>('/api/favourites', { method: 'GET' }),

  getFavourite: (id: string) =>
    request<Favourite>(`/api/favourites/${encodeURIComponent(id)}`, { method: 'GET' }),

  createFavourite: (draft: FavouriteDraft) =>
    request<Favourite>('/api/favourites', {
      method: 'POST',
      body: JSON.stringify(draft),
    }),

  updateFavourite: (id: string, patch: Partial<FavouriteDraft>) =>
    request<Favourite>(`/api/favourites/${encodeURIComponent(id)}`, {
      method: 'PUT',
      body: JSON.stringify(patch),
    }),

  deleteFavourite: (id: string) =>
    request<void>(`/api/favourites/${encodeURIComponent(id)}`, { method: 'DELETE' }),

  resolveFavourite: (query: string) =>
    request<{
      found: boolean;
      favourite: Favourite | null;
      score: number;
      method: string;
      alternatives: Favourite[];
    }>('/api/favourites/resolve', {
      method: 'POST',
      body: JSON.stringify({ query }),
    }),

  // --- observation --------------------------------------------------------

  observe: (page: SemanticPage | Record<string, unknown>) =>
    request<{
      page: {
        url: string;
        domain: string;
        title: string;
        page_type: string;
        summary: string;
        capabilities: Record<string, boolean>;
        element_count: number;
      };
      tools: Array<{ name: string; description: string }>;
      security: { is_suspicious: boolean; severity: string; categories: string[] };
      element_count: number;
    }>('/api/observe', {
      method: 'POST',
      body: JSON.stringify({ page_context: page }),
      timeoutMs: 15_000,
    }),
};
