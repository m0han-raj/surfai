/**
 * Pulling a list of results off a page.
 *
 * The fixtures are shaped like the real thing: a search grid where each result
 * is several divs deep, a job board built from list items, an article feed.
 * Grouping only by direct parent works on the last two and fails on the first,
 * which is the shape that matters most.
 *
 * Everything here is read from the DOM verbatim. A price is a fact printed on
 * the page, and a card showing a price the page never printed is not a
 * cosmetic defect.
 */

import { beforeEach, describe, expect, it } from 'vitest';
import { extractResultItems } from './result-items';

function stubLayout(height = 120) {
  Element.prototype.getBoundingClientRect = function (this: Element) {
    const style = window.getComputedStyle(this as HTMLElement);
    if (style.display === 'none') return { width: 0, height: 0 } as DOMRect;
    return { width: 200, height, top: 0, left: 0, bottom: height, right: 200 } as DOMRect;
  };
}

/** A search grid: each result several levels below the container. */
const SHOPPING_GRID = `
  <div class="s-main">
    <div class="s-result-list">
      <div data-component-type="s-search-result">
        <div class="puis-card"><div class="a-section">
          <img src="/img/nike-revolution.jpg" alt="Nike Revolution 7">
          <h2><a href="/dp/B0C1"><span>Nike Revolution 7 Running Shoes</span></a></h2>
          <span class="a-price"><span class="a-offscreen">&#8377;2,499</span></span>
          <span class="a-icon-alt">4.2 out of 5 stars</span>
        </div></div>
      </div>
      <div data-component-type="s-search-result">
        <div class="puis-card"><div class="a-section">
          <img src="/img/nike-court.jpg" alt="Nike Court Vision">
          <h2><a href="/dp/B0C2"><span>Nike Court Vision Low Sneakers</span></a></h2>
          <span class="a-price"><span class="a-offscreen">&#8377;899</span></span>
          <span class="a-icon-alt">4.0 out of 5 stars</span>
        </div></div>
      </div>
      <div data-component-type="s-search-result">
        <div class="puis-card"><div class="a-section">
          <img src="/img/puma-flex.jpg" alt="Puma Flex">
          <h2><a href="/dp/B0C3"><span>Puma Flex Renew Shoes</span></a></h2>
          <span class="a-price"><span class="a-offscreen">&#8377;1,799</span></span>
        </div></div>
      </div>
    </div>
  </div>`;

const JOB_BOARD = `
  <main><ul class="jobs">
    <li class="job-card">
      <h3>Senior Backend Engineer</h3>
      <span class="company">Acme Corp</span>
      <span class="location">Bengaluru, remote</span>
      <a href="/jobs/1">Apply</a>
    </li>
    <li class="job-card">
      <h3>Platform Engineer</h3>
      <span class="company">Globex</span>
      <span class="location">Pune</span>
      <a href="/jobs/2">Apply</a>
    </li>
  </ul></main>`;

const ARTICLE_FEED = `
  <main>
    <article><h2><a href="/p/1">Why carbonara has no cream</a></h2>
      <p>A short history of a Roman dish and the argument that will not die.</p></article>
    <article><h2><a href="/p/2">The case for guanciale</a></h2>
      <p>Pancetta is not a substitute, and here is the reason.</p></article>
  </main>`;

beforeEach(() => {
  stubLayout();
  document.body.innerHTML = '';
});

describe('a shopping grid', () => {
  it('finds every result even though each is several levels deep', () => {
    // Grouping strictly by direct parent misses this: the cards share a
    // container, but the text and price live three divs below it.
    document.body.innerHTML = SHOPPING_GRID;
    expect(extractResultItems()).toHaveLength(3);
  });

  it('reads the title from the heading rather than the whole card', () => {
    document.body.innerHTML = SHOPPING_GRID;
    const [first] = extractResultItems();

    expect(first.title).toBe('Nike Revolution 7 Running Shoes');
    expect(first.title).not.toContain('4.2 out of 5');
  });

  it('reads the price exactly as the page printed it', () => {
    document.body.innerHTML = SHOPPING_GRID;
    const prices = extractResultItems().map((item) => item.price);

    expect(prices).toEqual(['₹2,499', '₹899', '₹1,799']);
  });

  it('resolves links and images against the page, not as written', () => {
    document.body.innerHTML = SHOPPING_GRID;
    const [first] = extractResultItems();

    expect(first.url).toBe('http://localhost:3000/dp/B0C1');
    expect(first.image).toBe('http://localhost:3000/img/nike-revolution.jpg');
  });

  it('keeps the rating as a meta line', () => {
    document.body.innerHTML = SHOPPING_GRID;
    const [first] = extractResultItems();
    expect(first.meta?.join(' ')).toMatch(/4\.2 out of 5/);
  });

  it('leaves out an image a card does not have rather than borrowing one', () => {
    document.body.innerHTML = SHOPPING_GRID;
    document.querySelectorAll('img')[2].remove();

    expect(extractResultItems()[2].image).toBeUndefined();
  });
});

describe('other kinds of list', () => {
  it('reads a job board', () => {
    document.body.innerHTML = JOB_BOARD;
    const items = extractResultItems();

    expect(items.map((i) => i.title)).toEqual([
      'Senior Backend Engineer',
      'Platform Engineer',
    ]);
    expect(items[0].meta?.join(' ')).toContain('Acme Corp');
    expect(items[0].price).toBeUndefined();
  });

  it('reads an article feed', () => {
    document.body.innerHTML = ARTICLE_FEED;
    const items = extractResultItems();

    expect(items).toHaveLength(2);
    expect(items[0].title).toBe('Why carbonara has no cream');
    expect(items[0].meta?.join(' ')).toContain('Roman dish');
  });
});

describe('what it refuses to call a result list', () => {
  it('ignores a navigation menu', () => {
    // Repeated, yes. A result list, no: the entries are a few words with no
    // substance, which is what separates nav from content.
    document.body.innerHTML = `
      <nav><ul>
        <li><a href="/a">Home</a></li>
        <li><a href="/b">Deals</a></li>
        <li><a href="/c">Sell</a></li>
      </ul></nav>`;

    expect(extractResultItems()).toEqual([]);
  });

  it('ignores a single item, which is a page and not a list', () => {
    document.body.innerHTML = `
      <main><div class="product-card">
        <h1>Nike Revolution 7</h1><span>₹2,499</span>
        <p>A running shoe with a lot of text about it here to pass the length bar.</p>
      </div></main>`;

    expect(extractResultItems()).toEqual([]);
  });

  it('ignores items hidden from view', () => {
    document.body.innerHTML = ARTICLE_FEED;
    (document.querySelectorAll('article')[1] as HTMLElement).style.display = 'none';

    expect(extractResultItems()).toHaveLength(0);
  });

  it('returns nothing on a page with no list at all', () => {
    document.body.innerHTML = '<main><p>Just an ordinary paragraph of prose.</p></main>';
    expect(extractResultItems()).toEqual([]);
  });
});

describe('bounds', () => {
  it('caps how many it returns', () => {
    const card = '<li class="r"><h3>Item title here</h3><p>Enough text to count as real.</p></li>';
    document.body.innerHTML = `<main><ul>${card.repeat(200)}</ul></main>`;

    expect(extractResultItems().length).toBeLessThanOrEqual(40);
  });

  it('caps the length of what any one field can carry', () => {
    document.body.innerHTML = `
      <main><ul>
        <li class="r"><h3>${'long '.repeat(200)}</h3><p>Enough body text to count as a real result.</p></li>
        <li class="r"><h3>Second</h3><p>Enough body text to count as a real result.</p></li>
      </ul></main>`;

    expect(extractResultItems()[0].title.length).toBeLessThanOrEqual(200);
  });
});
