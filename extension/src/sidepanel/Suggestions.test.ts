/**
 * Starter prompts.
 *
 * They should reflect what SurfAI can actually do on the page in front of the
 * user, and degrade to general help rather than advertising capabilities that
 * are unavailable.
 */

import { describe, expect, it } from 'vitest';
import { readinessFor, suggestionsFor } from './components/Suggestions';
import type { PageInsight } from './usePageContext';

function insight(overrides: Partial<PageInsight> = {}): PageInsight {
  return {
    url: 'https://example.com/',
    domain: 'example.com',
    title: 'Example',
    pageType: 'content',
    capabilities: {},
    elementCount: 10,
    suspicious: false,
    tools: [],
    ...overrides,
  };
}

describe('suggestionsFor', () => {
  it('offers general help when there is no readable page', () => {
    const prompts = suggestionsFor(null);
    expect(prompts).toHaveLength(3);
    // Nothing here should promise something about "this page".
    expect(prompts.join(' ')).not.toMatch(/this page|this site/i);
  });

  it('offers a summary on an article', () => {
    expect(suggestionsFor(insight({ pageType: 'article' }))).toContain('Summarise this page');
  });

  it('offers search only where the page can search', () => {
    const withSearch = suggestionsFor(insight({ capabilities: { search: true } }));
    const without = suggestionsFor(insight({ capabilities: {} }));

    expect(withSearch).toContain('Find something on this site');
    expect(without).not.toContain('Find something on this site');
  });

  it('always offers saving the page', () => {
    expect(suggestionsFor(insight())).toContain('Save this page for later');
  });

  it('never offers more than three, so the empty state stays calm', () => {
    const crowded = suggestionsFor(
      insight({
        pageType: 'results',
        capabilities: { search: true, filters: true, results: true, pagination: true },
      }),
    );
    expect(crowded.length).toBeLessThanOrEqual(3);
  });

  it('returns distinct prompts', () => {
    const prompts = suggestionsFor(insight({ capabilities: { search: true, results: true } }));
    expect(new Set(prompts).size).toBe(prompts.length);
  });
});

describe('readinessFor', () => {
  // Opening the panel should answer "does it know where I am?" without the
  // user having to ask a question to find out.

  it('names the page it has read', () => {
    const state = readinessFor(
      insight({ domain: 'cooking.example.com', title: 'Classic Carbonara Recipe' }),
    );
    expect(state.title).toContain('Classic Carbonara Recipe');
    expect(state.ready).toBe(true);
  });

  it('falls back to the domain when a page has no title', () => {
    const state = readinessFor(insight({ title: '', domain: 'cooking.example.com' }));
    expect(state.title).toContain('cooking.example.com');
  });

  it('says how much of the page it actually took in', () => {
    // "I read the page" is a claim; the element count is the evidence, and it
    // is also the honest signal when a page turns out to be nearly empty.
    const state = readinessFor(insight({ elementCount: 24 }));
    expect(state.subtitle).toMatch(/24/);
  });

  it('is honest when there is no page to read', () => {
    const state = readinessFor(null);
    expect(state.ready).toBe(false);
    expect(state.title).not.toMatch(/I have read|I've read/i);
  });

  it('leads with the warning on a page that tried prompt injection', () => {
    // More important than anything else the chip could say about it.
    const state = readinessFor(insight({ suspicious: true }));
    expect(state.subtitle).toMatch(/instructions/i);
  });
});
