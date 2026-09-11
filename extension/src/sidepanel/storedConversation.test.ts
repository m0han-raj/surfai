/**
 * Reading a stored conversation back into the panel.
 *
 * The transcript comes back as the backend recorded it, and the panel renders
 * a slightly different shape. The interesting part is what has to survive the
 * round trip: a notice about the page changing is not decoration, it is the
 * reason the messages after it are about something else.
 */

import { describe, expect, it } from 'vitest';
import { anchorOf, toChatMessages } from './storedConversation';
import type { StoredConversation } from '../services/api';

function conversation(
  messages: StoredConversation['messages'],
): StoredConversation {
  return {
    id: 'c1',
    title: 'what is this page about?',
    created_at: '2026-09-11T10:00:00Z',
    updated_at: '2026-09-11T10:05:00Z',
    messages,
  };
}

function stored(
  role: 'user' | 'assistant' | 'notice',
  content: string,
  page_url: string | null = null,
): StoredConversation['messages'][number] {
  return { id: `m-${content}`, role, content, page_url, warnings: [], created_at: null };
}

describe('toChatMessages', () => {
  it('brings back both sides of the exchange in order', () => {
    const messages = toChatMessages(
      conversation([stored('user', 'what is this?'), stored('assistant', 'A recipe.')]),
    );

    expect(messages.map((m) => [m.role, m.content])).toEqual([
      ['user', 'what is this?'],
      ['assistant', 'A recipe.'],
    ]);
  });

  it('keeps a page-change notice as a notice', () => {
    // Rendering it as an assistant turn would put it in the conversation
    // rather than about it, and send it back to the model as history.
    const messages = toChatMessages(
      conversation([
        stored('user', 'what is this?'),
        stored('assistant', 'A recipe.'),
        stored('notice', 'Switched to flights.example.com.', 'https://flights.example.com/s'),
      ]),
    );

    expect(messages[2].role).toBe('notice');
    expect(messages[2].domain).toBe('flights.example.com');
  });

  it('carries warnings back so a hostile page stays flagged', () => {
    const withWarning = stored('assistant', 'It is a checkout page.');
    const messages = toChatMessages(
      conversation([
        stored('user', 'what does this say?'),
        { ...withWarning, warnings: ['This page tried to give the assistant instructions.'] },
      ]),
    );

    expect(messages[1].warnings).toEqual([
      'This page tried to give the assistant instructions.',
    ]);
  });

  it('gives every message a distinct id', () => {
    const messages = toChatMessages(
      conversation([stored('user', 'a'), stored('assistant', 'b'), stored('user', 'c')]),
    );
    expect(new Set(messages.map((m) => m.id)).size).toBe(3);
  });

  it('copes with an empty conversation', () => {
    expect(toChatMessages(conversation([]))).toEqual([]);
  });
});

describe('anchorOf', () => {
  it('is the page the conversation was last about', () => {
    // So switching tabs after reopening announces a change from the right
    // place, rather than from wherever the panel happens to be now.
    const resumed = conversation([
      stored('user', 'what is this?', 'https://cooking.example.com/carbonara'),
      stored('assistant', 'A recipe.', 'https://cooking.example.com/carbonara'),
      stored('notice', 'Switched.', 'https://flights.example.com/search'),
      stored('user', 'how much?', 'https://flights.example.com/search'),
    ]);

    expect(anchorOf(resumed)).toBe('flights.example.com');
  });

  it('is empty when nothing recorded a page', () => {
    expect(anchorOf(conversation([stored('user', 'hello')]))).toBe('');
  });

  it('ignores a url it cannot parse rather than throwing', () => {
    expect(anchorOf(conversation([stored('user', 'hi', 'not a url')]))).toBe('');
  });
});
