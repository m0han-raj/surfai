/**
 * Semantic DOM: a compact, model-readable description of the current page.
 *
 * Raw HTML is never sent anywhere. A typical product page is ~400KB of markup;
 * the snapshot it produces here is a few KB of labelled controls. That is not
 * only a cost decision -- a small model given raw HTML cannot reliably find the
 * search box, while given twenty labelled elements it can.
 *
 * Element ids (`e1`, `e2`, ...) are assigned per snapshot in document order and
 * held in a `WeakMap` so the executor can resolve an id back to the live node
 * without ever letting the model name a CSS selector. They are intentionally
 * *not* stable across snapshots: after the page changes, the agent must
 * re-observe, which is what keeps the loop honest.
 */

import type { SemanticElement, SemanticPage } from '@shared/types';
import {
  STRUCTURAL_SELECTOR,
  classify,
  describe,
  interactiveSelector,
  isVisible,
  visibleText,
} from './element-detector';

/** Budget for how many elements a single snapshot may carry. */
export const DEFAULT_MAX_ELEMENTS = 60;
const MAX_SUMMARY_CHARS = 1200;

/** Tags whose text is never page content. */
const IGNORED_TAGS = new Set([
  'SCRIPT',
  'STYLE',
  'NOSCRIPT',
  'TEMPLATE',
  'SVG',
  'IFRAME',
  'CANVAS',
  'HEAD',
  'META',
  'LINK',
]);

let registry = new WeakMap<Element, string>();
let elementsById = new Map<string, Element>();
let snapshotCounter = 0;

export interface SnapshotOptions {
  maxElements?: number;
  /** Include headings and labels, not only interactive controls. */
  includeStructure?: boolean;
  root?: ParentNode;
}

/** Resolve a semantic id back to its live DOM node. */
export function resolveElement(id: string): Element | null {
  const element = elementsById.get(id);
  if (!element) return null;
  // The node may have been detached since the snapshot was taken; the caller
  // treats that as a stale-target failure and re-observes.
  if (!element.isConnected) return null;
  return element;
}

export function knownIds(): string[] {
  return Array.from(elementsById.keys());
}

/** Clear the id map. Exported for tests. */
export function resetRegistry(): void {
  registry = new WeakMap<Element, string>();
  elementsById = new Map<string, Element>();
  snapshotCounter = 0;
}

/**
 * Score an element's importance, used to decide what survives the budget.
 *
 * Search boxes and submit buttons rank above decorative links, because when a
 * page has 300 controls the twenty that matter are the ones a user would use to
 * get something done.
 */
function priority(element: SemanticElement): number {
  let score = 0;

  switch (element.type) {
    case 'input':
    case 'textarea':
      score += 100;
      break;
    case 'select':
      score += 90;
      break;
    case 'button':
      score += 80;
      break;
    case 'checkbox':
    case 'radio':
      score += 70;
      break;
    case 'link':
      score += 40;
      break;
    case 'heading':
      score += 50 + (7 - (element.level ?? 6)) * 5;
      break;
    default:
      score += 10;
  }

  const label = `${element.text ?? ''} ${element.ariaLabel ?? ''} ${element.placeholder ?? ''}`;
  if (/search|find|query/i.test(label) || element.inputType === 'search') score += 60;
  if (/filter|sort|price|category|brand|location|experience/i.test(label)) score += 45;
  if (/next|previous|page|more/i.test(label)) score += 25;
  if (/submit|apply|go|continue/i.test(label)) score += 20;

  if (!element.visible) score -= 200;
  if (element.disabled) score -= 30;
  if (!element.text && !element.placeholder && !element.ariaLabel) score -= 25;

  return score;
}

/** Extract the page's main readable text, skipping chrome and navigation. */
export function extractSummary(root: ParentNode = document, maxChars = MAX_SUMMARY_CHARS): string {
  const main =
    (root as Document).querySelector?.('main, [role="main"], article, #main, #content') ??
    (root as Document).body ??
    null;

  const source = main ?? (root as Element);
  if (!source) return '';

  const walker = document.createTreeWalker(source, NodeFilter.SHOW_TEXT, {
    acceptNode(node: Node) {
      const parent = node.parentElement;
      if (!parent || IGNORED_TAGS.has(parent.tagName)) return NodeFilter.FILTER_REJECT;
      if (!(node.textContent || '').trim()) return NodeFilter.FILTER_REJECT;
      if (!isVisible(parent)) return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_ACCEPT;
    },
  });

  const parts: string[] = [];
  let total = 0;
  let node = walker.nextNode();
  while (node && total < maxChars) {
    const text = (node.textContent || '').replace(/\s+/g, ' ').trim();
    if (text.length > 1) {
      parts.push(text);
      total += text.length + 1;
    }
    node = walker.nextNode();
  }

  return parts.join(' ').slice(0, maxChars);
}

/**
 * Capture the current page as a semantic snapshot.
 *
 * Every call reassigns ids, so a snapshot is only valid until the next one.
 */
export function capturePage(options: SnapshotOptions = {}): SemanticPage {
  const {
    maxElements = DEFAULT_MAX_ELEMENTS,
    includeStructure = true,
    root = document,
  } = options;

  snapshotCounter += 1;
  registry = new WeakMap<Element, string>();
  elementsById = new Map<string, Element>();

  const selector = includeStructure
    ? `${interactiveSelector()},${STRUCTURAL_SELECTOR}`
    : interactiveSelector();

  const seen = new Set<Element>();
  const candidates: Array<{ element: Element; descriptor: SemanticElement }> = [];
  let nextId = 1;

  for (const element of Array.from(root.querySelectorAll(selector))) {
    if (seen.has(element)) continue;
    seen.add(element);
    if (IGNORED_TAGS.has(element.tagName)) continue;

    const type = classify(element);
    if (!type) continue;

    // Invisible controls are dropped: the agent must not target what a user
    // cannot see or reach.
    if (!isVisible(element)) continue;

    const id = `e${nextId++}`;
    const descriptor = describe(element, id, type);

    // A control with no label at all is unusable by name; keep it only if it
    // carries some other identifying signal.
    if (
      !descriptor.text &&
      !descriptor.placeholder &&
      !descriptor.ariaLabel &&
      !descriptor.name &&
      !descriptor.options &&
      descriptor.type !== 'input'
    ) {
      nextId -= 1;
      continue;
    }

    candidates.push({ element, descriptor });
  }

  // Rank, take the budget, then restore document order so positional language
  // in the prompt ("the first result") still lines up with the page.
  const ranked = [...candidates].sort(
    (a, b) => priority(b.descriptor) - priority(a.descriptor),
  );
  const kept = new Set(ranked.slice(0, maxElements).map((entry) => entry.element));
  const selected = candidates.filter((entry) => kept.has(entry.element));

  const elements: SemanticElement[] = [];
  for (const { element, descriptor } of selected) {
    registry.set(element, descriptor.id);
    elementsById.set(descriptor.id, element);
    elements.push(descriptor);
  }

  return {
    url: window.location.href,
    domain: window.location.hostname.toLowerCase(),
    title: document.title || visibleText(document.querySelector('h1') ?? document.body, 80),
    summary: extractSummary(root),
    elements,
    truncated: Math.max(0, candidates.length - elements.length),
    capturedAt: Date.now(),
  };
}

/** The id assigned to an element in the current snapshot, if any. */
export function idFor(element: Element): string | undefined {
  return registry.get(element);
}

export function snapshotId(): number {
  return snapshotCounter;
}
