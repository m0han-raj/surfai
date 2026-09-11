/**
 * Logical website capabilities inferred by the Tool Discovery agent.
 *
 * A tool is a *description* of something the site can do, bound to concrete
 * semantic element ids. Tools are never executable code: the planner selects a
 * tool, and the orchestrator expands it into validated BrowserActions.
 */

import type { BrowserAction } from './action-schema';

export type ToolParameterType = 'string' | 'number' | 'boolean' | 'enum';

export interface ToolParameter {
  name: string;
  type: ToolParameterType;
  required: boolean;
  description: string;
  /** Allowed values when `type` is `enum`. */
  options?: string[];
}

export interface DiscoveredTool {
  /** snake_case identifier, e.g. `search_products`. */
  name: string;
  description: string;
  parameters: ToolParameter[];
  /** Semantic element ids this tool operates on. */
  element_ids: string[];
  /** Deterministic expansion template; `{param}` placeholders are substituted. */
  action_template: BrowserAction[];
  /** 0..1 heuristic confidence from the discovery pass. */
  confidence: number;
}

export interface ToolDiscoveryResult {
  url: string;
  domain: string;
  tools: DiscoveredTool[];
  /** `heuristic` tools are found without an LLM call; `llm` are model-inferred. */
  source: 'heuristic' | 'llm' | 'merged';
}
