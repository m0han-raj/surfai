/**
 * How many results to put in front of someone at once.
 *
 * Twelve cards in a 400px panel is a wall you scroll past rather than an
 * answer you read. Five is a glance, and the rest are one click away, held in
 * the panel already: revealing them costs nothing, where asking for them in
 * chat would spend a request and a few thousand tokens to show data that never
 * left the browser.
 */

import { describe, expect, it } from 'vitest';
import { VISIBLE_BY_DEFAULT, splitResults } from './components/ResultCards';
import type { ResultItem } from '../types/agent';

function items(count: number): ResultItem[] {
  return Array.from({ length: count }, (_, i) => ({ title: `Item ${i + 1}` }));
}

describe('splitResults', () => {
  it('shows the first five', () => {
    const { visible, hidden } = splitResults(items(12), false);

    expect(visible.map((i) => i.title)).toEqual([
      'Item 1',
      'Item 2',
      'Item 3',
      'Item 4',
      'Item 5',
    ]);
    expect(hidden).toBe(7);
  });

  it('keeps the model’s ranking rather than re-sorting', () => {
    // It chose these, best first. Showing five means the best five.
    const ranked = [{ title: 'best' }, { title: 'second' }, ...items(10)];
    expect(splitResults(ranked, false).visible[0].title).toBe('best');
  });

  it('shows everything once asked', () => {
    const { visible, hidden } = splitResults(items(12), true);

    expect(visible).toHaveLength(12);
    expect(hidden).toBe(0);
  });

  it('offers nothing more when there is nothing more', () => {
    expect(splitResults(items(5), false).hidden).toBe(0);
    expect(splitResults(items(3), false).hidden).toBe(0);
  });

  it('handles an empty result set', () => {
    expect(splitResults([], false)).toEqual({ visible: [], hidden: 0 });
  });

  it('agrees with the constant it is built on', () => {
    expect(splitResults(items(VISIBLE_BY_DEFAULT + 1), false).visible).toHaveLength(
      VISIBLE_BY_DEFAULT,
    );
  });
});
