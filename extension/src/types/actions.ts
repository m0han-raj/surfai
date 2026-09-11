/**
 * Browser action types. Re-exported from the shared contract so the extension
 * and the backend cannot drift.
 */
export type {
  ActionType,
  BrowserAction,
  ActionResult,
  ScrollDirection,
  RiskLevel,
  RiskAssessment,
} from '@shared/action-schema';

export { ACTION_TYPES, RISK_LEVELS } from '@shared/action-schema';
