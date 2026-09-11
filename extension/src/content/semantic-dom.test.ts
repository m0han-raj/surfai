/**
 * Semantic DOM extraction (AC-04).
 *
 * jsdom does not do layout, so `getBoundingClientRect` returns zeros for
 * everything. `isVisible` falls back to `getClientRects()`, which is stubbed
 * here to report a real box unless a test explicitly hides an element -- that
 * keeps the visibility rules testable without a real browser.
 */

import { beforeEach, describe, expect, it } from 'vitest';
import { capturePage, extractSummary, resolveElement, resetRegistry } from './semantic-dom';
import { classify, isSensitive, labelFor } from './element-detector';

function stubLayout() {
  Element.prototype.getClientRects = function (this: Element) {
    const style = window.getComputedStyle(this as HTMLElement);
    if (style.display === 'none' || style.visibility === 'hidden') {
      return [] as unknown as DOMRectList;
    }
    return [{ width: 100, height: 20, top: 0, left: 0 }] as unknown as DOMRectList;
  };
  Element.prototype.getBoundingClientRect = function (this: Element) {
    const rects = this.getClientRects();
    if (rects.length === 0) {
      return { width: 0, height: 0, top: 0, left: 0, bottom: 0, right: 0 } as DOMRect;
    }
    return { width: 100, height: 20, top: 0, left: 0, bottom: 20, right: 100 } as DOMRect;
  };
}

const PRODUCT_PAGE = `
  <header><a href="/">Home</a></header>
  <main>
    <h1>Product Search</h1>
    <form>
      <label for="q">Search products</label>
      <input id="q" type="search" name="q" placeholder="Search products" />
      <button type="submit">Search</button>
    </form>
    <aside>
      <label for="price">Maximum price</label>
      <select id="price">
        <option value="">Any</option>
        <option value="50000">Under 50,000</option>
        <option value="80000">Under 80,000</option>
      </select>
      <input type="checkbox" id="stock" name="in_stock" aria-label="In stock only" />
    </aside>
    <section>
      <article class="product">
        <h2>Lenovo LOQ RTX 4060</h2>
        <p>Rs 74,990</p>
        <a href="/product/1">View product</a>
      </article>
      <article class="product">
        <h2>ASUS TUF RTX 4060</h2>
        <p>Rs 78,500</p>
        <a href="/product/2">View product</a>
      </article>
    </section>
    <nav><button>Next page</button></nav>
  </main>
`;

describe('capturePage', () => {
  beforeEach(() => {
    stubLayout();
    resetRegistry();
    document.title = 'Product Search';
    document.body.innerHTML = PRODUCT_PAGE;
  });

  it('identifies the controls an agent needs', () => {
    const page = capturePage();

    const search = page.elements.find((e) => e.inputType === 'search');
    expect(search, 'search input').toBeDefined();
    expect(search?.type).toBe('input');
    expect(search?.placeholder).toBe('Search products');

    const button = page.elements.find((e) => e.type === 'button' && e.text === 'Search');
    expect(button, 'search button').toBeDefined();

    const select = page.elements.find((e) => e.type === 'select');
    expect(select, 'price filter').toBeDefined();
    expect(select?.options).toEqual(['Any', 'Under 50,000', 'Under 80,000']);

    const checkbox = page.elements.find((e) => e.type === 'checkbox');
    expect(checkbox?.ariaLabel).toBe('In stock only');

    const links = page.elements.filter((e) => e.type === 'link');
    expect(links.length).toBeGreaterThanOrEqual(2);

    const next = page.elements.find((e) => e.text === 'Next page');
    expect(next, 'pagination').toBeDefined();
  });

  it('assigns sequential ids that resolve back to live nodes', () => {
    const page = capturePage();

    expect(page.elements[0].id).toBe('e1');
    for (const element of page.elements) {
      expect(element.id).toMatch(/^e\d+$/);
      expect(resolveElement(element.id)).not.toBeNull();
    }
  });

  it('does not expose raw HTML', () => {
    const page = capturePage();
    const serialised = JSON.stringify(page);

    expect(serialised).not.toContain('<article');
    expect(serialised).not.toContain('class="product"');
    expect(serialised).not.toContain('<input');
    // It is far smaller than the document it describes.
    expect(serialised.length).toBeLessThan(document.body.innerHTML.length * 2);
  });

  it('captures url, domain and title', () => {
    const page = capturePage();
    expect(page.title).toBe('Product Search');
    expect(page.url).toBe(window.location.href);
    expect(page.domain).toBe(window.location.hostname.toLowerCase());
    expect(page.capturedAt).toBeGreaterThan(0);
  });

  it('extracts readable page text without markup', () => {
    const page = capturePage();
    expect(page.summary).toContain('Lenovo LOQ RTX 4060');
    expect(page.summary).not.toContain('<');
  });

  it('skips hidden elements', () => {
    document.body.innerHTML += '<button style="display:none">Hidden action</button>';
    const page = capturePage();
    expect(page.elements.find((e) => e.text === 'Hidden action')).toBeUndefined();
  });

  it('never captures the value of a password field', () => {
    document.body.innerHTML +=
      '<input type="password" name="password" aria-label="Password" value="hunter2" />';
    const page = capturePage();

    const password = page.elements.find((e) => e.inputType === 'password');
    expect(password, 'password field is still visible to the agent').toBeDefined();
    expect(password?.value).toBeUndefined();
    expect((password as { sensitive?: boolean })?.sensitive).toBe(true);
    expect(JSON.stringify(page)).not.toContain('hunter2');
  });

  it('respects the element budget and reports what it dropped', () => {
    document.body.innerHTML =
      PRODUCT_PAGE + Array.from({ length: 80 }, (_, i) => `<button>Item ${i}</button>`).join('');

    const page = capturePage({ maxElements: 20 });
    expect(page.elements).toHaveLength(20);
    expect(page.truncated).toBeGreaterThan(0);
  });

  it('keeps high-value controls when the budget is tight', () => {
    document.body.innerHTML =
      PRODUCT_PAGE + Array.from({ length: 100 }, (_, i) => `<a href="/x${i}">Link ${i}</a>`).join('');

    const page = capturePage({ maxElements: 12 });
    // The search box must survive a hundred competing links.
    expect(page.elements.some((e) => e.inputType === 'search')).toBe(true);
  });

  it('returns element ids in document order', () => {
    const page = capturePage();
    const ids = page.elements.map((e) => Number(e.id.slice(1)));
    expect([...ids].sort((a, b) => a - b)).toEqual(ids);
  });

  it('handles an empty document', () => {
    document.body.innerHTML = '';
    const page = capturePage();
    expect(page.elements).toEqual([]);
    expect(page.truncated).toBe(0);
  });

  it('invalidates old ids on re-capture', () => {
    capturePage();
    document.body.innerHTML = '<button>Only button</button>';
    const second = capturePage();

    expect(second.elements).toHaveLength(1);
    expect(resolveElement('e9')).toBeNull();
  });
});

describe('element classification', () => {
  beforeEach(() => {
    stubLayout();
    document.body.innerHTML = '';
  });

  it.each([
    ['<button>x</button>', 'button'],
    ['<a href="/x">x</a>', 'link'],
    ['<input type="text" />', 'input'],
    ['<input type="checkbox" />', 'checkbox'],
    ['<input type="radio" />', 'radio'],
    ['<input type="submit" />', 'button'],
    ['<textarea></textarea>', 'textarea'],
    ['<select></select>', 'select'],
    ['<h2>x</h2>', 'heading'],
    ['<div role="button">x</div>', 'button'],
    ['<div role="searchbox">x</div>', 'input'],
    ['<div contenteditable="true">x</div>', 'textarea'],
  ])('classifies %s as %s', (html, expected) => {
    document.body.innerHTML = html;
    expect(classify(document.body.firstElementChild!)).toBe(expected);
  });

  it('ignores an anchor with no href', () => {
    document.body.innerHTML = '<a>not a link</a>';
    expect(classify(document.body.firstElementChild!)).toBeNull();
  });

  it.each([
    ['<button aria-label="Submit form">x</button>', 'Submit form'],
    ['<button>Visible text</button>', 'Visible text'],
    ['<label for="i">Email</label><input id="i" />', 'Email'],
    ['<input placeholder="Type here" />', 'Type here'],
    ['<button title="Tooltip label"></button>', 'Tooltip label'],
  ])('labels %s as "%s"', (html, expected) => {
    document.body.innerHTML = html;
    const element = document.querySelector('input, button')!;
    expect(labelFor(element)).toBe(expected);
  });

  it.each([
    '<input type="password" />',
    '<input name="user_password" />',
    '<input aria-label="CVV" />',
    '<input placeholder="Card number" />',
    '<input name="otp" />',
  ])('treats %s as sensitive', (html) => {
    document.body.innerHTML = html;
    expect(isSensitive(document.body.firstElementChild!)).toBe(true);
  });

  it('does not treat an ordinary field as sensitive', () => {
    document.body.innerHTML = '<input name="search" placeholder="Search products" />';
    expect(isSensitive(document.body.firstElementChild!)).toBe(false);
  });
});

describe('extractSummary', () => {
  beforeEach(() => {
    stubLayout();
  });

  it('prefers main content over page chrome', () => {
    document.body.innerHTML = `
      <nav>Navigation links here</nav>
      <main><p>The important article body.</p></main>
      <footer>Copyright notice</footer>`;

    const summary = extractSummary();
    expect(summary).toContain('The important article body.');
    expect(summary).not.toContain('Navigation links here');
  });

  it('skips script and style content', () => {
    document.body.innerHTML = `
      <main>
        <script>var secret = "do-not-include";</script>
        <style>.a { color: red; }</style>
        <p>Real content</p>
      </main>`;

    const summary = extractSummary();
    expect(summary).toContain('Real content');
    expect(summary).not.toContain('do-not-include');
  });

  it('caps the summary length', () => {
    document.body.innerHTML = `<main><p>${'word '.repeat(2000)}</p></main>`;
    expect(extractSummary(document, 500).length).toBeLessThanOrEqual(500);
  });
});
