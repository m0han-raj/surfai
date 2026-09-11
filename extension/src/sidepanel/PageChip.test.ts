/**
 * The one line in the header that says which page SurfAI can see.
 *
 * It got this wrong in a way that mattered: a page that had been read
 * perfectly well reported "Page unavailable" because a separate backend call
 * had failed. The chip is the only place the panel says what it is looking at,
 * so saying "nothing" when it does know is worse than saying nothing at all.
 */

import { describe, expect, it } from 'vitest';
import { chipStateFor } from './components/PageChip';
import type { PageInsight } from './usePageContext';

function insight(overrides: Partial<PageInsight> = {}): PageInsight {
  return {
    url: 'https://news.example.com/article/1',
    domain: 'news.example.com',
    title: 'An article',
    pageType: 'content',
    capabilities: {},
    elementCount: 24,
    suspicious: false,
    tools: [],
    ...overrides,
  };
}

describe('chipStateFor', () => {
  it('names the domain of a page it can see', () => {
    const state = chipStateFor(insight(), false, null);
    expect(state.kind).toBe('ok');
    expect(state.label).toBe('news.example.com');
  });

  it('still names the domain when only the backend analysis failed', () => {
    // usePageContext deliberately keeps the page it read and reports the
    // analysis failure alongside it. Preferring the error threw away the one
    // fact the chip exists to show.
    const state = chipStateFor(
      insight({ pageType: 'unknown', tools: [] }),
      false,
      'SurfAI could not analyse this page.',
    );

    expect(state.label).toBe('news.example.com');
    expect(state.kind).toBe('degraded');
  });

  it('explains the degradation on hover rather than in the label', () => {
    const state = chipStateFor(insight(), false, 'Backend not reachable.');
    expect(state.title).toContain('Backend not reachable.');
  });

  it('says the page is unavailable only when it really has nothing', () => {
    // A chrome:// page, the Web Store, or a tab that blocks content scripts.
    const state = chipStateFor(null, false, 'SurfAI could not read this page.');
    expect(state.kind).toBe('unavailable');
    expect(state.label).toBe('Page unavailable');
  });

  it('shows progress on the first read, not a false negative', () => {
    expect(chipStateFor(null, true, null).kind).toBe('loading');
  });

  it('keeps showing the current page while re-reading after a tab switch', () => {
    // Flashing "Reading page" over a domain it already knows is a worse
    // answer than the slightly stale domain.
    const state = chipStateFor(insight(), true, null);
    expect(state.label).toBe('news.example.com');
  });

  it('flags a hostile page over merely reporting its domain', () => {
    const state = chipStateFor(insight({ suspicious: true }), false, null);
    expect(state.kind).toBe('suspicious');
    expect(state.label).toBe('news.example.com');
  });

  it('flags a hostile page even when the analysis also failed', () => {
    // Security state outranks a degraded-analysis note; both are true, and
    // this is the one the user needs to see.
    const state = chipStateFor(insight({ suspicious: true }), false, 'Analysis failed.');
    expect(state.kind).toBe('suspicious');
  });
});
