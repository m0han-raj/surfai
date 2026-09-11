/**
 * Client half of the agent loop.
 *
 * The backend decides; this executes and re-observes. Keeping it free of React
 * means the whole loop -- including cancellation and confirmation -- is unit
 * testable against fakes.
 *
 *   directive = chat(message, page)
 *   while directive is an action:
 *       result = execute(directive.action)
 *       page   = capture()            <- the observe edge
 *       directive = continue(result, page)
 *
 * A `confirm` directive suspends the loop until the user answers, and `stop()`
 * ends it at the next checkpoint and tells the backend to cancel.
 */

import type { ActionResult, BrowserAction } from '@shared/action-schema';
import type { SemanticPage } from '@shared/types';
import type { Directive } from '../types/agent';
import { api, ApiError } from './api';
import {
  capturePage,
  executeAction,
  getTabContext,
  openUrl,
  waitForTabLoad,
} from './messaging';
import type { TabContext } from '../types/messages';

/** A prior turn, sent so a direct answer has conversational context. */
export interface ChatTurn {
  role: 'user' | 'assistant';
  content: string;
}

export interface RunnerCallbacks {
  onDirective: (directive: Directive) => void;
  onActivity: (label: string, status: 'running' | 'done' | 'failed' | 'info') => void;
  /** Resolve true to approve the pending action, false to decline. */
  onConfirmationRequired: (directive: Directive) => Promise<boolean>;
  onFinished: (directive: Directive) => void;
  onError: (message: string) => void;
}

const EMPTY_PAGE: Partial<SemanticPage> = { elements: [], summary: '', truncated: 0 };

/** A page snapshot plus the tab identity it came from. */
async function observe(maxElements?: number): Promise<{
  page: Record<string, unknown>;
  tab: TabContext;
  warning?: string;
}> {
  const tabResponse = await getTabContext();
  const tab: TabContext = tabResponse.data ?? { url: '', title: '' };

  const pageResponse = await capturePage(maxElements);
  if (!pageResponse.ok || !pageResponse.data) {
    // Still return the tab identity: the agent can say what it cannot see
    // rather than failing with no explanation.
    return {
      page: { ...EMPTY_PAGE, url: tab.url, title: tab.title },
      tab,
      warning: pageResponse.error ?? 'SurfAI could not read this page.',
    };
  }

  return { page: pageResponse.data as unknown as Record<string, unknown>, tab };
}

export class AgentRunner {
  private cancelled = false;
  private running = false;
  private taskId: string | null = null;

  constructor(
    private readonly callbacks: RunnerCallbacks,
    private readonly options: { maxElements?: number; actionTimeoutMs?: number } = {},
  ) {}

  get isRunning(): boolean {
    return this.running;
  }

  get currentTaskId(): string | null {
    return this.taskId;
  }

  /** Stop the loop and cancel the task server-side. History is preserved. */
  async stop(): Promise<void> {
    this.cancelled = true;
    const taskId = this.taskId;
    if (taskId) {
      try {
        await api.cancelTask(taskId);
      } catch {
        // Best effort: the local loop has already stopped, which is what
        // matters for the user. A stale server session times out on its own.
      }
    }
    this.callbacks.onActivity('Task cancelled', 'info');
    this.running = false;
  }

  /**
   * Send a user message.
   *
   * Most messages are answered directly by the backend and the loop never
   * starts; `handle` recognises that from the directive it gets back.
   */
  async send(message: string, history: ChatTurn[] = []): Promise<void> {
    if (this.running) {
      this.callbacks.onError('A task is already running. Stop it before starting another.');
      return;
    }

    this.cancelled = false;
    this.running = true;
    this.taskId = null;

    try {
      const { page, tab, warning } = await observe(this.options.maxElements);
      if (warning) this.callbacks.onActivity(warning, 'info');

      const directive = await api.chat({
        message,
        page_context: page,
        tab_context: tab,
        history,
      });
      await this.handle(directive);
    } catch (error) {
      this.fail(error);
    } finally {
      this.running = false;
    }
  }

  /** Run a task bound to a saved favourite. */
  async runFavourite(favouriteId: string, request: string): Promise<void> {
    if (this.running) {
      this.callbacks.onError('A task is already running. Stop it before starting another.');
      return;
    }

    this.cancelled = false;
    this.running = true;
    this.taskId = null;

    try {
      this.callbacks.onActivity('Opening saved favourite', 'running');
      const favourite = await api.getFavourite(favouriteId);

      const current = await getTabContext();
      if (favourite.url && current.data?.url !== favourite.url) {
        await openUrl(favourite.url);
        await waitForTabLoad();
      }

      const { page, tab } = await observe(this.options.maxElements);
      const directive = await api.createTask({
        request,
        page_context: page,
        tab_context: tab,
        favourite_id: favouriteId,
      });
      await this.handle(directive);
    } catch (error) {
      this.fail(error);
    } finally {
      this.running = false;
    }
  }

  /** Drive directives until the task reaches a terminal state. */
  private async handle(first: Directive): Promise<void> {
    let directive = first;
    this.taskId = directive.task_id || this.taskId;
    this.callbacks.onDirective(directive);

    // The favourite lives elsewhere: get there before the first action.
    if (directive.favourite_navigation) {
      this.callbacks.onActivity('Opening saved page', 'running');
      await openUrl(directive.favourite_navigation);
      await waitForTabLoad();
    }

    while (!this.cancelled) {
      if (directive.type === 'answer' || directive.type === 'error') {
        // No closing activity line. A directly answered question never ran a
        // step, and emitting one here would give it a steps affordance that
        // expands to nothing.
        this.callbacks.onFinished(directive);
        return;
      }

      if (directive.type === 'ask') {
        this.callbacks.onFinished(directive);
        return;
      }

      if (directive.type === 'confirm') {
        this.callbacks.onActivity('Awaiting confirmation', 'info');
        const approved = await this.callbacks.onConfirmationRequired(directive);
        if (this.cancelled) break;

        const { page } = await observe(this.options.maxElements);
        directive = await api.continueTask(this.taskId!, {
          page_context: page,
          confirmation: approved,
        });
        this.taskId = directive.task_id || this.taskId;
        this.callbacks.onDirective(directive);
        continue;
      }

      if (directive.type === 'action' && directive.action) {
        const result = await this.runAction(directive.action, directive.activity);
        if (this.cancelled) break;

        // Re-observe *after* the action: this is the loop's observe edge.
        if (result.url_changed) {
          await waitForTabLoad(10_000);
        }
        const { page } = await observe(this.options.maxElements);
        if (this.cancelled) break;

        directive = await api.continueTask(this.taskId!, {
          page_context: page,
          result,
        });
        this.taskId = directive.task_id || this.taskId;
        this.callbacks.onDirective(directive);
        continue;
      }

      this.callbacks.onError(`SurfAI received an unexpected instruction: ${directive.type}`);
      return;
    }

    if (this.cancelled) {
      this.callbacks.onFinished({
        ...directive,
        type: 'error',
        state: 'CANCELLED',
        message: 'Task cancelled.',
      });
    }
  }

  private async runAction(action: BrowserAction, activity: string): Promise<ActionResult> {
    this.callbacks.onActivity(activity || 'Executing action', 'running');

    const response = await executeAction(action, this.options.actionTimeoutMs);
    if (!response.ok || !response.data) {
      const error = response.error ?? 'The action could not be executed.';
      this.callbacks.onActivity(`Action failed: ${error}`, 'failed');
      return {
        success: false,
        action: action.action,
        target: action.target ?? null,
        url_changed: false,
        page_changed: false,
        error,
      };
    }

    const result = response.data;
    this.callbacks.onActivity(
      result.success ? 'Action completed' : `Action failed: ${result.error ?? 'unknown'}`,
      result.success ? 'done' : 'failed',
    );
    return result;
  }

  private fail(error: unknown): void {
    const message =
      error instanceof ApiError
        ? error.message
        : error instanceof Error
          ? error.message
          : 'Something went wrong.';
    this.callbacks.onActivity('Task failed', 'failed');
    this.callbacks.onError(message);
  }
}
