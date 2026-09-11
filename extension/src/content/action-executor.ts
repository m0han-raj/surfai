/**
 * Action executor: the only code in SurfAI that touches the page.
 *
 * Two properties make this safe:
 *
 *  1. It is a closed `switch` over seven verbs. There is no path from model
 *     output to `eval`, `Function`, `innerHTML`, or a script URL -- the worst a
 *     compromised planner can do is click a visible button, which a user could
 *     have clicked themselves.
 *  2. Targets arrive as snapshot ids and are resolved through the content
 *     script's own registry. The model never supplies a selector.
 *
 * Every action is time-boxed and returns a structured result, including on
 * failure, so the orchestrator can re-plan rather than hang.
 */

import type { ActionResult, ActionType, BrowserAction } from '@shared/action-schema';
import { resolveElement } from './semantic-dom';
import { observePageChange } from './page-observer';

export const DEFAULT_TIMEOUT_MS = 10_000;
const SETTLE_MS = 400;

/** Only these schemes may be navigated to. */
const ALLOWED_SCHEMES = new Set(['http:', 'https:']);

class ActionError extends Error {}

function result(
  partial: Omit<Partial<ActionResult>, 'action'> & { action: ActionType; success: boolean },
): ActionResult {
  return {
    target: null,
    url_changed: false,
    page_changed: false,
    error: null,
    ...partial,
  } as ActionResult;
}

/** Reject anything that is not a plain http(s) URL. */
function assertSafeUrl(raw: string): URL {
  let url: URL;
  try {
    url = new URL(raw, window.location.href);
  } catch {
    throw new ActionError(`'${raw}' is not a valid URL`);
  }
  if (!ALLOWED_SCHEMES.has(url.protocol)) {
    throw new ActionError(`Navigation to the '${url.protocol}' scheme is not allowed`);
  }
  return url;
}

function requireElement(id: string | null | undefined): Element {
  if (!id) throw new ActionError('This action requires a target element');
  const element = resolveElement(id);
  if (!element) {
    throw new ActionError(`Element '${id}' no longer exists on the page`);
  }
  const rect = (element as HTMLElement).getBoundingClientRect();
  if (rect.width === 0 && rect.height === 0 && element.getClientRects().length === 0) {
    throw new ActionError(`Element '${id}' is no longer visible`);
  }
  return element;
}

function scrollIntoView(element: Element): void {
  try {
    element.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'instant' as ScrollBehavior });
  } catch {
    element.scrollIntoView();
  }
}

/**
 * Fire the event sequence a real interaction produces.
 *
 * React and other frameworks track inputs through their own value setter, so
 * assigning `.value` directly updates the DOM but leaves component state stale
 * and the typed text is silently discarded on submit. Going through the native
 * prototype setter is what makes typing actually stick on modern sites.
 */
function setNativeValue(element: HTMLInputElement | HTMLTextAreaElement, value: string): void {
  const prototype =
    element instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(prototype, 'value')?.set;
  if (setter) {
    setter.call(element, value);
  } else {
    element.value = value;
  }
  element.dispatchEvent(new Event('input', { bubbles: true }));
  element.dispatchEvent(new Event('change', { bubbles: true }));
}

function isEditable(element: Element): element is HTMLInputElement | HTMLTextAreaElement {
  return element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement;
}

/**
 * Build a pointer event, degrading to a mouse event where PointerEvent is not
 * constructible. A missing constructor must not abort an otherwise valid click.
 */
function pointerEvent(type: string, options: MouseEventInit): Event {
  if (typeof PointerEvent === 'function') {
    try {
      return new PointerEvent(type, options);
    } catch {
      // fall through
    }
  }
  return new MouseEvent(type, options);
}

async function doClick(action: BrowserAction): Promise<ActionResult> {
  const element = requireElement(action.target);
  scrollIntoView(element);
  await delay(50);

  (element as HTMLElement).focus?.();
  // A plain .click() misses handlers bound to pointer/mouse events, so send the
  // full sequence a real press produces.
  // `view` is deliberately omitted: it is not needed by event handlers and not
  // portable across DOM implementations.
  const options: MouseEventInit = { bubbles: true, cancelable: true };
  element.dispatchEvent(pointerEvent('pointerdown', options));
  element.dispatchEvent(new MouseEvent('mousedown', options));
  element.dispatchEvent(pointerEvent('pointerup', options));
  element.dispatchEvent(new MouseEvent('mouseup', options));
  (element as HTMLElement).click();

  return result({ success: true, action: 'CLICK', target: action.target ?? null });
}

async function doType(action: BrowserAction): Promise<ActionResult> {
  const element = requireElement(action.target);
  const value = action.value ?? '';

  scrollIntoView(element);
  (element as HTMLElement).focus?.();
  await delay(30);

  if (isEditable(element)) {
    setNativeValue(element, '');
    setNativeValue(element, value);
  } else if ((element as HTMLElement).isContentEditable) {
    (element as HTMLElement).textContent = value;
    element.dispatchEvent(new InputEvent('input', { bubbles: true }));
  } else {
    throw new ActionError(
      `Element '${action.target}' is a ${element.tagName.toLowerCase()} and cannot accept text`,
    );
  }

  // Many search boxes submit on Enter rather than through a button; send the
  // key so a form without a visible submit still works.
  element.dispatchEvent(
    new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 13, bubbles: true }),
  );
  element.dispatchEvent(
    new KeyboardEvent('keyup', { key: 'Enter', code: 'Enter', keyCode: 13, bubbles: true }),
  );

  return result({ success: true, action: 'TYPE', target: action.target ?? null });
}

async function doSelect(action: BrowserAction): Promise<ActionResult> {
  const element = requireElement(action.target);
  if (!(element instanceof HTMLSelectElement)) {
    throw new ActionError(`Element '${action.target}' is not a dropdown`);
  }

  const wanted = (action.value ?? '').trim().toLowerCase();
  const option = Array.from(element.options).find(
    (candidate) =>
      (candidate.textContent || '').trim().toLowerCase() === wanted ||
      candidate.value.trim().toLowerCase() === wanted,
  );

  if (!option) {
    const available = Array.from(element.options)
      .map((o) => (o.textContent || o.value).trim())
      .slice(0, 15)
      .join(', ');
    throw new ActionError(
      `'${action.value}' is not an option. Available options: ${available}`,
    );
  }

  element.value = option.value;
  element.dispatchEvent(new Event('input', { bubbles: true }));
  element.dispatchEvent(new Event('change', { bubbles: true }));

  return result({ success: true, action: 'SELECT', target: action.target ?? null });
}

async function doScroll(action: BrowserAction): Promise<ActionResult> {
  const direction = action.direction ?? 'down';
  const amount = Math.round(window.innerHeight * 0.8);

  switch (direction) {
    case 'up':
      window.scrollBy({ top: -amount, behavior: 'instant' as ScrollBehavior });
      break;
    case 'down':
      window.scrollBy({ top: amount, behavior: 'instant' as ScrollBehavior });
      break;
    case 'top':
      window.scrollTo({ top: 0, behavior: 'instant' as ScrollBehavior });
      break;
    case 'bottom':
      window.scrollTo({ top: document.body.scrollHeight, behavior: 'instant' as ScrollBehavior });
      break;
    default:
      throw new ActionError(`Unknown scroll direction '${direction}'`);
  }

  await delay(200);
  return result({ success: true, action: 'SCROLL', page_changed: true });
}

async function doNavigate(action: BrowserAction): Promise<ActionResult> {
  const url = assertSafeUrl(action.value ?? '');
  const before = window.location.href;
  window.location.assign(url.href);
  // The page is being torn down; report optimistically and let the side panel
  // re-capture once the new document is ready.
  return result({
    success: true,
    action: 'NAVIGATE',
    url_changed: url.href !== before,
    page_changed: true,
  });
}

/** Structured read of the page or one element. */
async function doExtract(action: BrowserAction): Promise<ActionResult> {
  if (action.target) {
    const element = requireElement(action.target);
    return result({
      success: true,
      action: 'EXTRACT',
      target: action.target,
      data: {
        text: (element.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 4000),
        href: element.getAttribute('href') ?? undefined,
      },
    });
  }

  return result({ success: true, action: 'EXTRACT', data: extractPageData() });
}

/**
 * Read repeated result items, then fall back to page text.
 *
 * Most result pages render a list of structurally similar cards. Finding the
 * largest such group yields clean per-item records ("name, price, link")
 * instead of one undifferentiated blob, which is what makes the agent's final
 * answer specific.
 */
export function extractPageData(): Record<string, unknown> {
  const items = extractRepeatedItems();
  const headings = Array.from(document.querySelectorAll('h1,h2,h3'))
    .filter((h) => (h.textContent || '').trim())
    .slice(0, 15)
    .map((h) => (h.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 120));

  return {
    url: window.location.href,
    title: document.title,
    headings,
    items: items.slice(0, 25),
    item_count: items.length,
    text: items.length ? undefined : mainText(),
  };
}

function mainText(maxChars = 4000): string {
  const main = document.querySelector('main, [role="main"], article') ?? document.body;
  return (main?.textContent || '').replace(/\s+/g, ' ').trim().slice(0, maxChars);
}

function extractRepeatedItems(): Array<Record<string, string>> {
  const candidates = document.querySelectorAll(
    '[data-testid*="card" i],[class*="card" i],[class*="product" i],[class*="result" i],' +
      '[class*="item" i],[class*="job" i],[class*="listing" i],article,li',
  );

  // Group by parent: a real result list shares one container.
  const groups = new Map<Element, Element[]>();
  for (const element of Array.from(candidates)) {
    const parent = element.parentElement;
    if (!parent) continue;
    const group = groups.get(parent) ?? [];
    group.push(element);
    groups.set(parent, group);
  }

  let best: Element[] = [];
  for (const group of groups.values()) {
    const visible = group.filter((el) => {
      const rect = (el as HTMLElement).getBoundingClientRect();
      // Height rules out tiny chips and badges -- but only when layout has
      // actually run. A zero height means "not measured", not "too small".
      const bigEnough = rect.height === 0 || rect.height > 30;
      return bigEnough && (el.textContent || '').trim().length > 20;
    });
    if (visible.length >= 2 && visible.length > best.length) best = visible;
  }

  return best.slice(0, 40).map((element) => {
    const record: Record<string, string> = {};
    const heading = element.querySelector('h1,h2,h3,h4,[class*="title" i],[class*="name" i]');
    const title = (heading?.textContent || '').replace(/\s+/g, ' ').trim();
    const text = (element.textContent || '').replace(/\s+/g, ' ').trim();

    record.title = (title || text).slice(0, 150);

    const price = /(?:₹|rs\.?|\$|€|£)\s?[\d,]+(?:\.\d{1,2})?/i.exec(text);
    if (price) record.price = price[0].trim();

    const link = element.querySelector('a[href]')?.getAttribute('href');
    if (link) {
      try {
        record.url = new URL(link, window.location.href).href;
      } catch {
        record.url = link;
      }
    }

    if (!title && text) record.text = text.slice(0, 300);
    return record;
  });
}

async function doWait(action: BrowserAction): Promise<ActionResult> {
  const ms = Math.min(Math.max(action.timeout_ms ?? 1000, 0), 30_000);
  await delay(ms);
  return result({ success: true, action: 'WAIT' });
}

export function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function withTimeout<T>(promise: Promise<T>, ms: number, label: string): Promise<T> {
  return Promise.race([
    promise,
    new Promise<T>((_, reject) =>
      setTimeout(() => reject(new ActionError(`${label} timed out after ${ms}ms`)), ms),
    ),
  ]);
}

/**
 * Execute one validated action.
 *
 * Never throws: a failure is a structured result so the orchestrator can decide
 * whether to retry, re-plan, or stop.
 */
export async function executeAction(
  action: BrowserAction,
  timeoutMs: number = DEFAULT_TIMEOUT_MS,
): Promise<ActionResult> {
  const started = performance.now();
  const urlBefore = window.location.href;
  const observer = observePageChange();

  try {
    let outcome: ActionResult;
    switch (action.action) {
      case 'CLICK':
        outcome = await withTimeout(doClick(action), timeoutMs, 'CLICK');
        break;
      case 'TYPE':
        outcome = await withTimeout(doType(action), timeoutMs, 'TYPE');
        break;
      case 'SELECT':
        outcome = await withTimeout(doSelect(action), timeoutMs, 'SELECT');
        break;
      case 'SCROLL':
        outcome = await withTimeout(doScroll(action), timeoutMs, 'SCROLL');
        break;
      case 'NAVIGATE':
        outcome = await withTimeout(doNavigate(action), timeoutMs, 'NAVIGATE');
        break;
      case 'EXTRACT':
        outcome = await withTimeout(doExtract(action), timeoutMs, 'EXTRACT');
        break;
      case 'WAIT':
        outcome = await withTimeout(doWait(action), timeoutMs, 'WAIT');
        break;
      default:
        // Unreachable given validation, but an unknown verb must never be
        // guessed at.
        throw new ActionError(`Unsupported action '${String(action.action)}'`);
    }

    // Give the page a moment to react, then report what actually changed.
    if (action.action !== 'WAIT' && action.action !== 'EXTRACT') {
      await delay(SETTLE_MS);
    }

    outcome.url_changed = outcome.url_changed || window.location.href !== urlBefore;
    outcome.page_changed = outcome.page_changed || observer.changed();
    outcome.duration_ms = Math.round(performance.now() - started);
    return outcome;
  } catch (error) {
    return result({
      success: false,
      action: action.action,
      target: action.target ?? null,
      error: error instanceof Error ? error.message : 'Unknown execution error',
      duration_ms: Math.round(performance.now() - started),
      url_changed: window.location.href !== urlBefore,
      page_changed: observer.changed(),
    });
  } finally {
    observer.stop();
  }
}
