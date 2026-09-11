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

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  at: number;
  /** Non-fatal notices, such as a prompt-injection warning. */
  warnings?: string[];
  error?: boolean;
}
