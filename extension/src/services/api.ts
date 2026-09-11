/**
 * Backend HTTP client.
 *
 * The extension holds no model credentials: every LLM call goes through the
 * backend, which reads its key from the environment. That is why the panel
 * talks to `localhost:8000` rather than to an inference endpoint directly.
 */

import type { Directive } from '../types/agent';
import type { Favourite, FavouriteDraft } from '../types/favourites';
import type { Task } from '@shared/types';
import type { ActionResult } from '@shared/action-schema';
import type { SemanticPage } from '@shared/types';
import type { TabContext } from '../types/messages';
import { getSettings } from './storage';

export const DEFAULT_BACKEND_URL = 'http://localhost:8000';

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number = 0,
    readonly isNetwork = false,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

async function baseUrl(): Promise<string> {
  const settings = await getSettings();
  return (settings.backendUrl || DEFAULT_BACKEND_URL).replace(/\/+$/, '');
}

async function request<T>(
  path: string,
  options: RequestInit & { timeoutMs?: number } = {},
): Promise<T> {
  const { timeoutMs = 180_000, ...init } = options;
  const url = `${await baseUrl()}${path}`;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  let response: Response;
  try {
    response = await fetch(url, {
      ...init,
      signal: controller.signal,
      headers: {
        'Content-Type': 'application/json',
        ...(init.headers ?? {}),
      },
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new ApiError('The backend took too long to respond.', 0, true);
    }
    throw new ApiError(
      'Could not reach the SurfAI backend. Check that it is running and that the ' +
        'address in Settings is correct.',
      0,
      true,
    );
  } finally {
    clearTimeout(timer);
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
    const detail =
      (body as { detail?: string })?.detail ?? `The backend returned ${response.status}.`;
    throw new ApiError(detail, response.status);
  }

  return body as T;
}

// --- health ---------------------------------------------------------------

export interface HealthResponse {
  status: string;
  app: string;
  environment: string;
  database: { connected: boolean; error: string | null };
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
  health: () => request<HealthResponse>('/health', { method: 'GET', timeoutMs: 5000 }),

  llmHealth: () => request<LlmHealthResponse>('/health/llm', { method: 'GET', timeoutMs: 15000 }),

  // --- chat and tasks -----------------------------------------------------

  chat: (payload: {
    message: string;
    page_context: SemanticPage | Record<string, unknown>;
    tab_context: TabContext;
    history?: Array<{ role: 'user' | 'assistant'; content: string }>;
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
