/**
 * Parsing the markdown the model actually sends.
 *
 * Every sample here is real output captured from gpt-oss-120b during this
 * project, not invented. The panel used to render it verbatim, so a reply
 * about "**Paris**" arrived with its asterisks showing.
 *
 * The parser is deliberately separate from the rendering. It is where all the
 * decisions live, including the ones that matter for safety, and a pure
 * function is where they can be pinned down.
 */

import { describe, expect, it } from 'vitest';
import { parseMarkdown } from './Markdown';

describe('paragraphs and inline formatting', () => {
  it('reads plain text as a paragraph', () => {
    expect(parseMarkdown('The capital of France is Paris.')).toEqual([
      { type: 'paragraph', content: [{ type: 'text', value: 'The capital of France is Paris.' }] },
    ]);
  });

  it('reads bold', () => {
    const [block] = parseMarkdown('The capital of France is **Paris**.');
    expect(block).toEqual({
      type: 'paragraph',
      content: [
        { type: 'text', value: 'The capital of France is ' },
        { type: 'bold', value: 'Paris' },
        { type: 'text', value: '.' },
      ],
    });
  });

  it('reads italic without mistaking it for bold', () => {
    const [block] = parseMarkdown('that is *probably* right');
    expect(block).toMatchObject({
      content: [
        { type: 'text', value: 'that is ' },
        { type: 'italic', value: 'probably' },
        { type: 'text', value: ' right' },
      ],
    });
  });

  it('reads inline code', () => {
    const [block] = parseMarkdown('set `LLM_BASE_URL` first');
    expect(block).toMatchObject({
      content: [
        { type: 'text', value: 'set ' },
        { type: 'code', value: 'LLM_BASE_URL' },
        { type: 'text', value: ' first' },
      ],
    });
  });

  it('leaves an unmatched marker as literal text', () => {
    // Prices and maths are full of loose asterisks and underscores.
    const [block] = parseMarkdown('2 * 3 * 4 is 24');
    expect(block).toMatchObject({ content: [{ type: 'text', value: '2 * 3 * 4 is 24' }] });
  });

  it('separates paragraphs on a blank line', () => {
    expect(parseMarkdown('First line.\n\nSecond line.')).toHaveLength(2);
  });

  it('keeps a single newline inside one paragraph', () => {
    const blocks = parseMarkdown('First line.\nStill the same paragraph.');
    expect(blocks).toHaveLength(1);
  });
});

describe('lists', () => {
  it('reads a bullet list, formatting included', () => {
    // Verbatim from a page summary this session.
    const blocks = parseMarkdown(
      '- **Purpose:** Search results for wireless mice.\n- **Products shown:** 3 items',
    );

    expect(blocks).toEqual([
      {
        type: 'list',
        ordered: false,
        items: [
          [
            { type: 'bold', value: 'Purpose:' },
            { type: 'text', value: ' Search results for wireless mice.' },
          ],
          [
            { type: 'bold', value: 'Products shown:' },
            { type: 'text', value: ' 3 items' },
          ],
        ],
      },
    ]);
  });

  it('reads a numbered list', () => {
    const blocks = parseMarkdown('1. Logitech M185 - $19.99\n2. Razer Pro Click - $89.00');
    expect(blocks[0]).toMatchObject({ type: 'list', ordered: true });
    expect((blocks[0] as { items: unknown[] }).items).toHaveLength(2);
  });

  it('accepts the other bullet characters models use', () => {
    expect(parseMarkdown('* one\n* two')[0]).toMatchObject({ type: 'list', ordered: false });
  });

  it('ends a list at a following paragraph', () => {
    const blocks = parseMarkdown('- one\n- two\n\nAnd that is all.');
    expect(blocks.map((b) => b.type)).toEqual(['list', 'paragraph']);
  });
});

describe('headings and code blocks', () => {
  it('reads a heading', () => {
    expect(parseMarkdown('## Page Summary')[0]).toEqual({
      type: 'heading',
      level: 2,
      content: [{ type: 'text', value: 'Page Summary' }],
    });
  });

  it('caps heading depth so a reply cannot out-shout the panel', () => {
    // The panel's own headers are h2. A model emitting `#` should not render
    // larger than the interface around it.
    expect(parseMarkdown('# Huge')[0]).toMatchObject({ level: 2 });
  });

  it('reads a fenced code block and keeps its whitespace', () => {
    const blocks = parseMarkdown('```python\ndef f():\n    return 1\n```');
    expect(blocks[0]).toEqual({
      type: 'code',
      language: 'python',
      value: 'def f():\n    return 1',
    });
  });

  it('does not parse markdown inside a code block', () => {
    const blocks = parseMarkdown('```\n- **not a list**\n```');
    expect(blocks[0]).toMatchObject({ type: 'code', value: '- **not a list**' });
  });

  it('closes an unterminated code block at the end of the text', () => {
    // Output truncated by max_tokens arrives exactly like this.
    expect(parseMarkdown('```\nhalf a thing')[0]).toMatchObject({
      type: 'code',
      value: 'half a thing',
    });
  });
});

describe('links, and what makes them safe', () => {
  it('reads a link', () => {
    const [block] = parseMarkdown('see [the docs](https://example.com/docs)');
    expect(block).toMatchObject({
      content: [
        { type: 'text', value: 'see ' },
        { type: 'link', href: 'https://example.com/docs', value: 'the docs' },
      ],
    });
  });

  it('refuses a javascript: url, keeping the text', () => {
    // Model output is shaped by page content, and a page will try this.
    const [block] = parseMarkdown('[click me](javascript:alert(1))');
    expect(block).toMatchObject({ content: [{ type: 'text', value: 'click me' }] });
  });

  it('refuses data: and other schemes it cannot vouch for', () => {
    for (const href of ['data:text/html,<script>', 'vbscript:x', 'file:///etc/passwd']) {
      const [block] = parseMarkdown(`[x](${href})`);
      expect(block).toMatchObject({ content: [{ type: 'text', value: 'x' }] });
    }
  });

  it('allows http and mailto alongside https', () => {
    for (const href of ['http://localhost:8010/health', 'mailto:a@b.com']) {
      const [block] = parseMarkdown(`[x](${href})`);
      expect(block).toMatchObject({ content: [{ type: 'link', href }] });
    }
  });
});

describe('untrusted content', () => {
  it('treats html as text, never as markup', () => {
    // The renderer builds React elements rather than setting innerHTML, so
    // this can only ever be text. Pinned here because the day someone reaches
    // for dangerouslySetInnerHTML, this is what should stop them.
    const [block] = parseMarkdown('<script>alert(1)</script>');
    expect(block).toMatchObject({
      content: [{ type: 'text', value: '<script>alert(1)</script>' }],
    });
  });

  it('treats an img onerror payload as text', () => {
    const [block] = parseMarkdown('<img src=x onerror=alert(1)>');
    expect(block).toMatchObject({ type: 'paragraph' });
    expect(JSON.stringify(block)).toContain('onerror=alert(1)');
  });

  it('survives empty and whitespace-only input', () => {
    expect(parseMarkdown('')).toEqual([]);
    expect(parseMarkdown('   \n\n  ')).toEqual([]);
  });
});
