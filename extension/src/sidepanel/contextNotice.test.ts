/**
 * Telling the user, in the conversation, that the page underneath it changed.
 *
 * The runner already reads whichever tab is in front of you at the moment you
 * send a message, so the answers were always about the right page. What was
 * missing was any sign of it, which left you unable to tell whether SurfAI had
 * noticed. This is that sign.
 *
 * The awkward part is not saying it, it is saying it once. Hunting through
 * tabs for something is normal, and a notice per tab would bury the
 * conversation in notices about pages you never asked anything about.
 */

import { describe, expect, it } from 'vitest';
import { withContextNotice } from './contextNotice';
import type { ChatMessage } from '../types/agent';

const at = 1_700_000_000_000;

function turn(id: string, role: 'user' | 'assistant', content: string): ChatMessage {
  return { id, role, content, at };
}

const conversation: ChatMessage[] = [
  turn('u1', 'user', 'what is this page about?'),
  turn('a1', 'assistant', 'A recipe for carbonara.'),
];

describe('withContextNotice', () => {
  it('says nothing while the page has not changed', () => {
    const next = withContextNotice(conversation, 'cooking.example.com', 'cooking.example.com', at);
    expect(next).toBe(conversation);
  });

  it('says nothing in an empty panel', () => {
    // Nothing to re-anchor, and the header chip already names the page. A
    // notice here would be the first thing SurfAI ever said to you.
    expect(withContextNotice([], 'flights.example.com', 'cooking.example.com', at)).toEqual([]);
  });

  it('announces the new page once a conversation is underway', () => {
    const next = withContextNotice(conversation, 'flights.example.com', 'cooking.example.com', at);

    expect(next).toHaveLength(3);
    expect(next[2]).toMatchObject({ role: 'notice', domain: 'flights.example.com' });
    expect(next[2].content).toContain('flights.example.com');
  });

  it('replaces the notice rather than stacking them', () => {
    // Three tabs on the way to the one you want should leave one line, not
    // three. The last one is the only one that is true.
    let next = withContextNotice(conversation, 'a.example.com', 'cooking.example.com', at);
    next = withContextNotice(next, 'b.example.com', 'cooking.example.com', at);
    next = withContextNotice(next, 'c.example.com', 'cooking.example.com', at);

    expect(next.filter((m) => m.role === 'notice')).toHaveLength(1);
    expect(next[2]).toMatchObject({ domain: 'c.example.com' });
  });

  it('withdraws the notice when you return to the page you were discussing', () => {
    // Nothing changed after all, so nothing should claim it did.
    const switched = withContextNotice(conversation, 'flights.example.com', 'cooking.example.com', at);
    const back = withContextNotice(switched, 'cooking.example.com', 'cooking.example.com', at);

    expect(back).toEqual(conversation);
  });

  it('leaves a notice alone once a message follows it', () => {
    // By then it is a fact about the conversation, not a live indicator.
    const switched = withContextNotice(conversation, 'flights.example.com', 'cooking.example.com', at);
    const asked = [...switched, turn('u2', 'user', 'how much?')];

    // The anchor has moved on with the question.
    const next = withContextNotice(asked, 'flights.example.com', 'flights.example.com', at);
    expect(next).toBe(asked);

    const away = withContextNotice(asked, 'hotels.example.com', 'flights.example.com', at);
    expect(away.filter((m) => m.role === 'notice')).toHaveLength(2);
    expect(away[2]).toMatchObject({ domain: 'flights.example.com' });
  });

  it('ignores a tab with no readable page rather than announcing nothing', () => {
    // chrome:// pages, the Web Store, a blank tab. "Switched to" with no
    // name is worse than silence.
    for (const empty of ['', undefined]) {
      expect(withContextNotice(conversation, empty as string, 'cooking.example.com', at))
        .toBe(conversation);
    }
  });

  it('gives each notice its own id so React can keep them apart', () => {
    const first = withContextNotice(conversation, 'a.example.com', 'cooking.example.com', at);
    const asked = [...first, turn('u2', 'user', 'x')];
    const second = withContextNotice(asked, 'b.example.com', 'a.example.com', at);

    const ids = second.filter((m) => m.role === 'notice').map((m) => m.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it('reads as SurfAI speaking, not as a log line', () => {
    const [, , notice] = withContextNotice(conversation, 'flights.example.com', 'cooking.example.com', at);
    expect(notice.content).toMatch(/I'll answer about/i);
  });
});
