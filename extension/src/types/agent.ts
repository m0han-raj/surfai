import type { AgentState } from '@shared/types';
import type { BrowserAction, RiskAssessment } from '@shared/action-schema';

export type { AgentState, Task, TaskAction, TaskStatus } from '@shared/types';
export { AGENT_STATES, TERMINAL_STATES } from '@shared/types';

/** What the backend tells the extension to do next. */
export type DirectiveType = 'action' | 'confirm' | 'answer' | 'ask' | 'error';

export interface Directive {
  type: DirectiveType;
  task_id: string;
  state: AgentState;
  activity: string;
  step: number;
  max_steps: number;
  action: BrowserAction | null;
  risk: RiskAssessment | null;
  message: string;
  data?: unknown;
  warnings: string[];
  /** Present on /api/chat responses. */
  intent?: string;
  favourite?: Record<string, unknown> | null;
  favourites?: Record<string, unknown>[] | null;
  favourite_navigation?: string;
}

/** One line in the Agent Activity log. */
export interface ActivityEntry {
  id: string;
  label: string;
  status: 'running' | 'done' | 'failed' | 'info';
  detail?: string;
  at: number;
}

/** One step SurfAI took, shown folded under the reply that used it. */
export interface MessageStep {
  id: string;
  label: string;
  status: 'running' | 'done' | 'failed' | 'info';
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  at: number;
  /** Non-fatal notices, such as a prompt-injection warning. */
  warnings?: string[];
  error?: boolean;
  /** Present only when SurfAI acted on the page to produce this reply. */
  steps?: MessageStep[];
  /** The reply is still being produced; renders as a live status line. */
  pending?: boolean;
}
