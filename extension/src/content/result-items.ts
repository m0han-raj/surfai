/**
 * Reading a page's result list into records the panel can render as cards.
 *
 * Every field here is copied out of the DOM. None of it is ever asked of a
 * model: a price is a fact printed on the page, and a card showing a price the
 * page never printed is not a cosmetic defect. The model's only say in this is
 * which of these items answer the question, by index.
 *
 * Deliberately about "results" rather than products. The same shape describes
 * an Amazon grid, a job board, a news feed and a docs search, which is what
 * makes this a browsing assistant rather than a shopping one.
 */

/** One row of a result list, as the page printed it. */
export interface ResultItem {
  title: string;
  price?: string;
  image?: string;
  url?: string;
  /** Whatever else the card carried: a rating, a company, a location, a date. */
  meta?: string[];
}

const MAX_ITEMS = 40;
const MAX_FIELD = 200;
const MAX_META_LINES = 3;

/** Containers a result usually sits in, across the kinds of site we see. */
const CANDIDATE_SELECTOR = [
  '[data-component-type*="result" i]',
  '[data-testid*="card" i]',
  '[data-testid*="result" i]',
  '[class*="card" i]',
  '[class*="product" i]',
  '[class*="result" i]',
  '[class*="listing" i]',
  '[class*="job" i]',
  '[class*="item" i]',
  'article',
  'li',
].join(',');

/** Prices, in the currencies a card is likely to print. */
const PRICE = /(?:₹|rs\.?|\$|€|£|¥)\s?[\d,]+(?:\.\d{1,2})?/i;

const TITLE_SELECTOR = 'h1,h2,h3,h4,[class*="title" i],[class*="name" i],[class*="heading" i]';

function clean(text: string | null | undefined): string {
  return (text || '').replace(/\s+/g, ' ').trim();
}

function isVisible(element: Element): boolean {
  const style = window.getComputedStyle(element as HTMLElement);
  if (style.display === 'none' || style.visibility === 'hidden') return false;
  const rect = (element as HTMLElement).getBoundingClientRect();
  // A zero height means layout has not run, not that the element is small.
  return rect.height === 0 || rect.height > 30;
}

function absolute(value: string | null): string | undefined {
  if (!value) return undefined;
  try {
    return new URL(value, window.location.href).href;
  } catch {
    return undefined;
  }
}

/**
 * The ancestor `depth` levels up, used to group siblings-in-spirit.
 *
 * Grouping strictly by direct parent works on a job board built from `li`s and
 * fails on a search grid, where each result is a wrapper around a wrapper
 * around the content. Walking up a few levels finds the container they really
 * share.
 */
function ancestor(element: Element, depth: number): Element | null {
  let current: Element | null = element;
  for (let i = 0; i < depth && current; i += 1) current = current.parentElement;
  return current;
}

/**
 * Does this look like a list of results rather than a menu?
 *
 * Navigation is repeated too. What separates it is not length -- a terse
 * product card can be "Lenovo LOQ / Rs 74,990 / View", barely twenty
 * characters -- but structure: a result carries a heading or a price, and
 * "Home", "Deals", "Sell" carry neither. The length bar is only there to
 * throw out fragments.
 */
function looksSubstantial(element: Element): boolean {
  const text = clean(element.textContent);
  if (text.length < 12) return false;
  return Boolean(element.querySelector(TITLE_SELECTOR)) || PRICE.test(text);
}

/** The largest group of similar, substantial, visible siblings on the page. */
function findGroup(): Element[] {
  const candidates = Array.from(document.querySelectorAll(CANDIDATE_SELECTOR));
  let best: Element[] = [];

  // Several grouping depths, because how deeply a card is wrapped varies by
  // site. The largest plausible group wins.
  for (const depth of [1, 2, 3]) {
    const groups = new Map<Element, Element[]>();
    for (const element of candidates) {
      const container = ancestor(element, depth);
      if (!container) continue;
      const group = groups.get(container) ?? [];
      group.push(element);
      groups.set(container, group);
    }

    for (const group of groups.values()) {
      // Only the outermost of a nested run: a card matching the selector and
      // its inner wrapper would otherwise both count as results.
      const outermost = group.filter(
        (element) => !group.some((other) => other !== element && other.contains(element)),
      );
      const usable = outermost.filter((el) => isVisible(el) && looksSubstantial(el));
      if (usable.length >= 2 && usable.length > best.length) best = usable;
    }
  }

  return best;
}

/** Lines worth keeping beside the title: a rating, a company, a location. */
function metaFor(element: Element, title: string, price: string | undefined): string[] {
  const lines: string[] = [];

  for (const child of Array.from(element.querySelectorAll('span,p,div,small,time'))) {
    if (child.querySelector('span,p,div,small,time')) continue; // leaves only
    const text = clean(child.textContent);
    if (!text || text.length < 3 || text.length > 120) continue;
    if (text === title || text === price) continue;
    if (title.includes(text) || (price && price.includes(text))) continue;
    if (lines.includes(text)) continue;
    lines.push(text.slice(0, MAX_FIELD));
    if (lines.length >= MAX_META_LINES) break;
  }

  return lines;
}

function toItem(element: Element): ResultItem | null {
  const text = clean(element.textContent);
  const title = clean(element.querySelector(TITLE_SELECTOR)?.textContent) || text;
  if (!title) return null;

  const price = PRICE.exec(text)?.[0].trim();
  const image = element.querySelector('img[src]')?.getAttribute('src');
  const href = element.querySelector('a[href]')?.getAttribute('href');
  const meta = metaFor(element, title, price);

  return {
    title: title.slice(0, MAX_FIELD),
    ...(price ? { price } : {}),
    ...(absolute(image ?? null) ? { image: absolute(image ?? null) } : {}),
    ...(absolute(href ?? null) ? { url: absolute(href ?? null) } : {}),
    ...(meta.length ? { meta } : {}),
  };
}

/** The page's result list, or an empty array if it does not have one. */
export function extractResultItems(): ResultItem[] {
  return findGroup()
    .slice(0, MAX_ITEMS)
    .map(toItem)
    .filter((item): item is ResultItem => item !== null);
}
