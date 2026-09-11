/**
 * Canonical browser action contract.
 *
 * This file is the single source of truth shared by the Chrome extension and,
 * by mirrored definition, the FastAPI backend (`backend/app/browser/action_schema.py`).
 * Any change here must be mirrored there and covered by a test.
 */

export const ACTION_TYPES = [
  'CLICK',
  'TYPE',
  'SELECT',
  'SCROLL',
  'NAVIGATE',
  'EXTRACT',
  'WAIT',
] as const;

export type ActionType = (typeof ACTION_TYPES)[number];

export type ScrollDirection = 'up' | 'down' | 'top' | 'bottom';

/**
 * An action proposed by the planner. `target` is always a semantic element id
 * (`e12`) produced by the content script -- never a raw CSS selector and never
 * a fragment of script. NAVIGATE is the only action carrying a URL.
 */
export interface BrowserAction {
  action: ActionType;
  /** Semantic element id from the current page snapshot. */
  target?: string | null;
  /** Text to type, option to select, or the URL for NAVIGATE. */
  value?: string | null;
  /** Direction for SCROLL. */
  direction?: ScrollDirection | null;
  /** Milliseconds for WAIT. */
  timeout_ms?: number | null;
  /** Short human-readable justification shown in the activity log. */
  reason?: string | null;
}

export interface ActionResult {
  success: boolean;
  action: ActionType;
  target?: string | null;
  url_changed: boolean;
  page_changed: boolean;
  /** Populated by EXTRACT. */
  data?: unknown;
  error?: string | null;
  duration_ms?: number;
}

export const RISK_LEVELS = ['LOW', 'MEDIUM', 'HIGH'] as const;
export type RiskLevel = (typeof RISK_LEVELS)[number];

export interface RiskAssessment {
  level: RiskLevel;
  requires_confirmation: boolean;
  /** Stable machine-readable category, e.g. `PURCHASE`. */
  category: string;
  /** Sentence shown verbatim in the confirmation dialog. */
  explanation: string;
}
