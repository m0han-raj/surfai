/**
 * End-to-end check of the extractor against the real demo pages (AC-04).
 *
 * These load `demo/` straight off disk, so a change that breaks extraction on
 * the pages the README tells people to try fails the build. jsdom does not run
 * layout, so the stub reports a plausible box for anything not hidden by CSS.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { capturePage, resetRegistry } from './semantic-dom';

const DEMO = resolve(__dirname, '../../../demo');
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
    if (this.getClientRects().length === 0) {
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
}

/**
 * Load a demo page's markup and run its scripts.
 *
 * The `<script src>` tags are resolved from disk and evaluated, so the tests
 * see the same rendered result a browser would -- product cards included.
 */
function loadDemoPage(relativePath: string) {
  const html = readFileSync(resolve(DEMO, relativePath), 'utf-8');
  const bodyMatch = /<body[^>]*>([\s\S]*)<\/body>/i.exec(html);
  const titleMatch = /<title>([^<]*)<\/title>/i.exec(html);

  document.documentElement.innerHTML = `<head></head><body>${bodyMatch?.[1] ?? ''}</body>`;
  document.title = titleMatch?.[1] ?? '';

  const dir = relativePath.includes('/') ? relativePath.replace(/\/[^/]+$/, '') : '';

  for (const script of Array.from(document.querySelectorAll('script'))) {
    const src = script.getAttribute('src');
    const code = src
      ? readFileSync(resolve(DEMO, dir, src), 'utf-8')
      : (script.textContent ?? '');
    script.remove();
    // eslint-disable-next-line no-new-func
    new Function(code).call(window);
  }
}

describe('demo product search page', () => {
  beforeEach(() => {
    stubLayout();
    resetRegistry();
    loadDemoPage('product-search/index.html');
  });

  it('renders products from the catalogue', () => {
    expect(document.querySelectorAll('.product-card').length).toBeGreaterThan(0);
  });

  it('exposes the search input, search button, filters and product links (AC-04)', () => {
    const page = capturePage({ maxElements: 80 });

    const search = page.elements.find((e) => e.inputType === 'search');
    expect(search, 'search input').toBeDefined();
    expect(search?.type).toBe('input');

    const searchButton = page.elements.find(
      (e) => e.type === 'button' && /^search$/i.test(e.text ?? ''),
    );
    expect(searchButton, 'search button').toBeDefined();

    const selects = page.elements.filter((e) => e.type === 'select');
    expect(selects.length, 'filter dropdowns').toBeGreaterThanOrEqual(3);

    const price = selects.find((e) => /price/i.test(`${e.text} ${e.ariaLabel} ${e.name}`));
    expect(price?.options).toContain('Under 80,000');

    const productLinks = page.elements.filter(
      (e) => e.type === 'link' && /view product/i.test(e.text ?? ''),
    );
    expect(productLinks.length, 'product links').toBeGreaterThan(0);

    const next = page.elements.find((e) => /next page/i.test(e.text ?? ''));
    expect(next, 'pagination control').toBeDefined();
  });

  it('sends no markup to the model', () => {
    const page = capturePage();
    const serialised = JSON.stringify(page);

    // Structure, class names and inline styles never leave the page.
    expect(serialised).not.toContain('class=');
    expect(serialised).not.toContain('<article');
    expect(serialised).not.toContain('<div');
    expect(serialised).not.toContain('product-card');
    expect(serialised).not.toContain('style=');
  });

  it('captures product names in the page summary', () => {
    const page = capturePage();
    expect(page.summary).toMatch(/Lenovo|ASUS|Acer/);
  });

  it('finds the high-risk Buy now control so the classifier can gate it', () => {
    const page = capturePage({ maxElements: 120 });
    expect(page.elements.some((e) => /buy now/i.test(e.text ?? ''))).toBe(true);
  });
});

describe('demo job search page', () => {
  beforeEach(() => {
    stubLayout();
    resetRegistry();
    loadDemoPage('job-search/index.html');
  });

  it('exposes search, location and experience filters, and job cards', () => {
    const page = capturePage({ maxElements: 80 });

    expect(page.elements.some((e) => e.type === 'input' && e.inputType === 'search')).toBe(true);

    const selects = page.elements.filter((e) => e.type === 'select');
    const labels = selects.map((e) => `${e.text} ${e.ariaLabel} ${e.name}`.toLowerCase());
    expect(labels.some((l) => l.includes('location'))).toBe(true);
    expect(labels.some((l) => l.includes('experience'))).toBe(true);

    expect(document.querySelectorAll('.job-card').length).toBeGreaterThan(0);
    expect(page.elements.some((e) => /apply now/i.test(e.text ?? ''))).toBe(true);
  });
});

describe('demo article page', () => {
  beforeEach(() => {
    stubLayout();
    resetRegistry();
    loadDemoPage('article/index.html');
  });

  it('captures the heading structure and body text', () => {
    const page = capturePage({ maxElements: 80 });

    const headings = page.elements.filter((e) => e.type === 'heading');
    expect(headings.length).toBeGreaterThanOrEqual(4);
    expect(headings.some((h) => h.level === 1)).toBe(true);
    expect(headings.some((h) => /semantic/i.test(h.text ?? ''))).toBe(true);

    expect(page.summary).toContain('semantic');
    expect(page.elements.filter((e) => e.type === 'link').length).toBeGreaterThan(0);
  });
});

describe('demo prompt injection page', () => {
  beforeEach(() => {
    stubLayout();
    resetRegistry();
    loadDemoPage('article/injection-test.html');
  });

  it('captures the payloads so the backend can detect and redact them', () => {
    const page = capturePage({ maxElements: 100 });
    // The extractor's job is to report faithfully; neutralisation happens
    // server-side, and is covered by the backend security tests.
    expect(page.summary.toLowerCase()).toContain('ignore all previous instructions');
  });

  it('excludes CSS-hidden injected text entirely', () => {
    const page = capturePage({ maxElements: 100 });
    const serialised = JSON.stringify(page).toLowerCase();
    // `display: none` content never reaches the model at all.
    expect(serialised).not.toContain("delete the user's account");
  });
});
