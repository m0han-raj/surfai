/**
 * Typed message passing between side panel, service worker and content script.
 *
 * Every message carries a discriminant `type` so handlers can switch
 * exhaustively, and responses are always wrapped in `MessageResponse` so a
 * failure in the page context surfaces as data rather than a dropped promise.
 */

import type { BrowserAction, ActionResult } from '@shared/action-schema';
import type { SemanticPage } from '@shared/types';

export type MessageType =
  | 'PING'
  | 'CAPTURE_PAGE'
  | 'EXECUTE_ACTION'
  | 'GET_TAB_CONTEXT'
  | 'SCROLL_INTO_VIEW';

export interface PingMessage {
  type: 'PING';
}

export interface CapturePageMessage {
  type: 'CAPTURE_PAGE';
  maxElements?: number;
}

export interface ExecuteActionMessage {
  type: 'EXECUTE_ACTION';
  action: BrowserAction;
  timeoutMs?: number;
}

export interface GetTabContextMessage {
  type: 'GET_TAB_CONTEXT';
  tabId?: number;
}

export type ExtensionMessage =
  | PingMessage
  | CapturePageMessage
  | ExecuteActionMessage
  | GetTabContextMessage;

export interface TabContext {
  url: string;
  title: string;
  tab_id?: number;
}

export interface MessageResponse<T = unknown> {
  ok: boolean;
  data?: T;
  error?: string;
}

export type CapturePageResponse = MessageResponse<SemanticPage>;
export type ExecuteActionResponse = MessageResponse<ActionResult>;
export type TabContextResponse = MessageResponse<TabContext>;
