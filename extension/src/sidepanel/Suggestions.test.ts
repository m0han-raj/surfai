/**
 * Starter prompts.
 *
 * They should reflect what SurfAI can actually do on the page in front of the
 * user, and degrade to general help rather than advertising capabilities that
 * are unavailable.
 */

import { describe, expect, it } from 'vitest';
import { suggestionsFor } from './components/Suggestions';
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
