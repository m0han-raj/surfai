/**
 * Action executor (AC-06, AC-12, AC-16).
 *
 * The safety-critical assertions here are the negative ones: no path from an
 * action object to script execution, and no way to navigate to a non-http(s)
 * scheme.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { executeAction } from './action-executor';
import { capturePage, resetRegistry } from './semantic-dom';
import type { BrowserAction } from '@shared/action-schema';

/**
 * jsdom performs no layout, so every box would otherwise be zero-sized. The
 * stub reports a plausible box (tall enough to read as a real result card) for
 * anything not explicitly hidden by CSS.
 */
const STUB_HEIGHT = 60;

function stubLayout() {
  Element.prototype.getClientRects = function (this: Element) {
    const style = window.getComputedStyle(this as HTMLElement);
    if (style.display === 'none' || style.visibility === 'hidden') {
      return [] as unknown as DOMRectList;
    }
    return [{ width: 200, height: STUB_HEIGHT }] as unknown as DOMRectList;
  };
  Element.prototype.getBoundingClientRect = function (this: Element) {
    const rects = this.getClientRects();
    if (rects.length === 0) {
      return { width: 0, height: 0, top: 0, left: 0, bottom: 0, right: 0 } as DOMRect;
    }
    return {
      width: 200,
      height: STUB_HEIGHT,
      top: 0,
      left: 0,
      bottom: STUB_HEIGHT,
      right: 200,
    } as DOMRect;
  };
  Element.prototype.scrollIntoView = vi.fn();
  window.scrollTo = vi.fn();
  window.scrollBy = vi.fn();
}

/** Capture a snapshot and return a lookup from label text to element id. */
function snapshotIds(): Record<string, string> {
  const page = capturePage();
  const map: Record<string, string> = {};
  for (const element of page.elements) {
    const key = element.text ?? element.placeholder ?? element.ariaLabel ?? element.id;
    if (key && !(key in map)) map[key] = element.id;
  }
  return map;
}

describe('executeAction', () => {
  beforeEach(() => {
    stubLayout();
    resetRegistry();
    document.body.innerHTML = `
      <input id="q" type="search" aria-label="Search products" />
      <button id="go">Search</button>
      <select id="price" aria-label="Maximum price">
        <option value="">Any</option>
        <option value="80000">Under 80,000</option>
      </select>
      <textarea id="notes" aria-label="Notes"></textarea>
      <main>
        <h1>Products</h1>
        <div>
          <article><h2>Lenovo LOQ</h2><p>Rs 74,990</p><a href="/p/1">View</a></article>
          <article><h2>ASUS TUF</h2><p>Rs 78,500</p><a href="/p/2">View</a></article>
        </div>
      </main>`;
  });

  // --- happy paths ------------------------------------------------------

  it('types into an input and fires framework-visible events', async () => {
    const ids = snapshotIds();
    const input = document.getElementById('q') as HTMLInputElement;
    const inputEvents = vi.fn();
    input.addEventListener('input', inputEvents);

    const result = await executeAction({
      action: 'TYPE',
      target: ids['Search products'],
      value: 'RTX 4060 laptop',
    });

    expect(result.success).toBe(true);
    expect(input.value).toBe('RTX 4060 laptop');
    expect(inputEvents).toHaveBeenCalled();
  });

  it('clicks a button', async () => {
    const ids = snapshotIds();
    const clicked = vi.fn();
    document.getElementById('go')!.addEventListener('click', clicked);

    const result = await executeAction({ action: 'CLICK', target: ids.Search });

    expect(result.success).toBe(true);
    expect(clicked).toHaveBeenCalled();
    expect(result.duration_ms).toBeGreaterThanOrEqual(0);
  });

  it('selects a dropdown option by its visible text', async () => {
    const ids = snapshotIds();
    const select = document.getElementById('price') as HTMLSelectElement;

    const result = await executeAction({
      action: 'SELECT',
      target: ids['Maximum price'],
      value: 'Under 80,000',
    });

    expect(result.success).toBe(true);
    expect(select.value).toBe('80000');
  });

  it('scrolls', async () => {
    const result = await executeAction({ action: 'SCROLL', direction: 'bottom' });
    expect(result.success).toBe(true);
    expect(window.scrollTo).toHaveBeenCalled();
  });

  it('waits for the requested duration', async () => {
    const started = Date.now();
    const result = await executeAction({ action: 'WAIT', timeout_ms: 60 });
    expect(result.success).toBe(true);
    expect(Date.now() - started).toBeGreaterThanOrEqual(50);
  });

  it('extracts repeated result items as structured records', async () => {
    const result = await executeAction({ action: 'EXTRACT' });

    expect(result.success).toBe(true);
    const data = result.data as { items: Array<Record<string, string>>; headings: string[] };
    expect(data.items.length).toBeGreaterThanOrEqual(2);
    expect(data.items[0].title).toContain('Lenovo LOQ');
    expect(data.items[0].price).toContain('74,990');
    expect(data.headings).toContain('Products');
  });

  it('extracts a single element when given a target', async () => {
    const ids = snapshotIds();
    const result = await executeAction({ action: 'EXTRACT', target: ids.View });
    expect(result.success).toBe(true);
    expect((result.data as { text: string }).text).toBe('View');
  });

  // --- AC-16: no arbitrary code execution -------------------------------

  it.each([
    'javascript:alert(1)',
    'JavaScript:fetch("/steal")',
    'data:text/html,<script>alert(1)</script>',
    'file:///etc/passwd',
    'vbscript:msgbox(1)',
  ])('refuses to navigate to %s', async (url) => {
    const result = await executeAction({ action: 'NAVIGATE', value: url });

    expect(result.success).toBe(false);
    expect(result.error).toBeTruthy();
    expect(result.url_changed).toBe(false);
  });

  it('treats a typed script payload as literal text, never as code', async () => {
    const ids = snapshotIds();
    const input = document.getElementById('q') as HTMLInputElement;
    const payload = '<script>window.__pwned = true</script>';

    const result = await executeAction({
      action: 'TYPE',
      target: ids['Search products'],
      value: payload,
    });

    expect(result.success).toBe(true);
    // It landed in the field as text and executed nothing.
    expect(input.value).toBe(payload);
    expect((window as unknown as { __pwned?: boolean }).__pwned).toBeUndefined();
    expect(document.querySelectorAll('script')).toHaveLength(0);
  });

  it('rejects an unknown action verb', async () => {
    const result = await executeAction({ action: 'EVAL' } as unknown as BrowserAction);
    expect(result.success).toBe(false);
    expect(result.error).toContain('Unsupported action');
  });

  // --- failure reporting ------------------------------------------------

  it('reports a stale element id rather than throwing', async () => {
    const ids = snapshotIds();
    document.getElementById('go')!.remove();

    const result = await executeAction({ action: 'CLICK', target: ids.Search });

    expect(result.success).toBe(false);
    expect(result.error).toContain('no longer exists');
    expect(result.action).toBe('CLICK');
  });

  it('reports an unknown element id', async () => {
    const result = await executeAction({ action: 'CLICK', target: 'e999' });
    expect(result.success).toBe(false);
    expect(result.error).toContain('e999');
  });

  it('reports an invalid dropdown option with the available choices', async () => {
    const ids = snapshotIds();
    const result = await executeAction({
      action: 'SELECT',
      target: ids['Maximum price'],
      value: 'Under 10',
    });

    expect(result.success).toBe(false);
    expect(result.error).toContain('Under 80,000');
  });

  it('refuses to type into a button', async () => {
    const ids = snapshotIds();
    const result = await executeAction({ action: 'TYPE', target: ids.Search, value: 'x' });
    expect(result.success).toBe(false);
    expect(result.error).toContain('cannot accept text');
  });

  it('requires a target where one is needed', async () => {
    const result = await executeAction({ action: 'CLICK' });
    expect(result.success).toBe(false);
    expect(result.error).toContain('requires a target');
  });

  it('times out instead of hanging', async () => {
    const ids = snapshotIds();
    // A click handler that never yields would otherwise block the loop.
    document.getElementById('go')!.addEventListener('click', () => {
      const until = Date.now() + 120;
      while (Date.now() < until) {
        /* block */
      }
    });

    const result = await executeAction({ action: 'CLICK', target: ids.Search }, 1);
    expect(result.success).toBe(false);
    expect(result.error).toContain('timed out');
  });

  // --- page change detection (AC-07) ------------------------------------

  it('reports that the page changed after a mutation', async () => {
    const ids = snapshotIds();
    document.getElementById('go')!.addEventListener('click', () => {
      const node = document.createElement('div');
      node.textContent = 'New results';
      document.body.appendChild(node);
    });

    const result = await executeAction({ action: 'CLICK', target: ids.Search });
    expect(result.success).toBe(true);
    expect(result.page_changed).toBe(true);
  });

  it('reports no change when nothing happened', async () => {
    const ids = snapshotIds();
    const result = await executeAction({ action: 'CLICK', target: ids.Search });
    expect(result.success).toBe(true);
    expect(result.page_changed).toBe(false);
  });
});
