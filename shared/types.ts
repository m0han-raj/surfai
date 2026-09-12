/**
 * Cross-cutting types shared by extension and backend.
 */

export const AGENT_STATES = [
  'IDLE',
  'ANALYZING',
  'OBSERVING',
  'PLANNING',
  'WAITING_CONFIRMATION',
  'EXECUTING',
  'VERIFYING',
  'REPLANNING',
  'COMPLETED',
  'FAILED',
  'CANCELLED',
] as const;

export type AgentState = (typeof AGENT_STATES)[number];

/** Terminal states: the orchestrator will not schedule further steps. */
export const TERMINAL_STATES: readonly AgentState[] = [
  'COMPLETED',
  'FAILED',
  'CANCELLED',
];

export type SemanticElementType =
  | 'input'
  | 'textarea'
  | 'button'
  | 'link'
  | 'select'
  | 'checkbox'
  | 'radio'
  | 'form'
  | 'heading'
  | 'text'
  | 'image'
  | 'listitem';

export interface SemanticElement {
  /** Snapshot-stable id, e.g. `e7`. */
  id: string;
  type: SemanticElementType;
  tag: string;
  text?: string;
  placeholder?: string;
  ariaLabel?: string;
  name?: string;
  role?: string;
  inputType?: string;
  value?: string;
  href?: string;
  options?: string[];
  visible: boolean;
  disabled?: boolean;
  /** Heading depth for `heading` elements. */
  level?: number;
}

/** One row of a page's result list, as the page printed it. */
export interface ResultItem {
  title: string;
  price?: string;
  image?: string;
  url?: string;
  meta?: string[];
}

export interface SemanticPage {
  url: string;
  domain: string;
  title: string;
  /** Short excerpt of main content -- never the full document. */
  summary: string;
  elements: SemanticElement[];
  /** Number of elements dropped by the budget, for transparency. */
  truncated: number;
  /**
   * The page's result list, if it has one.
   *
   * Separate from `elements`, which is what the agent can act on. These are
   * what the page is showing, and they are what the panel renders as cards.
   */
  items?: ResultItem[];
  capturedAt: number;
}

export interface Favourite {
  id: string;
  name: string;
  url: string;
  domain: string;
  intent: string;
  description: string;
  preferences: Record<string, unknown>;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export type TaskStatus = AgentState;

export interface TaskAction {
  id: string;
  task_id: string;
  step_number: number;
  action_type: string;
  target?: string | null;
  arguments: Record<string, unknown>;
  result: Record<string, unknown>;
  status: string;
  created_at: string;
}

export interface Task {
  id: string;
  request: string;
  status: TaskStatus;
  current_url?: string | null;
  summary?: string | null;
  created_at: string;
  completed_at?: string | null;
  actions?: TaskAction[];
}
