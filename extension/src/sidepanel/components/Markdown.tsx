/**
 * Rendering the markdown an assistant reply arrives in.
 *
 * Deliberately not a markdown library. Model output is shaped by whatever page
 * the user is on, so a reply is untrusted text, and the usual pairing of a
 * parser with `dangerouslySetInnerHTML` plus a sanitiser puts the safety of the
 * panel on the sanitiser being right. Building React elements instead means
 * text is escaped by React itself and there is no HTML path at all.
 *
 * The subset is what models actually emit in chat: bold, italic, inline code,
 * fenced code, headings, bullet and numbered lists, and links. Anything else
 * survives as the text it was written as, which is the right answer for a
 * chat reply and a poor one for a document renderer. This is a chat reply.
 */

import type { ReactNode } from 'react';

export type Inline =
  | { type: 'text'; value: string }
  | { type: 'bold'; value: string }
  | { type: 'italic'; value: string }
  | { type: 'code'; value: string }
  | { type: 'link'; href: string; value: string };

export type Block =
  | { type: 'paragraph'; content: Inline[] }
  | { type: 'heading'; level: number; content: Inline[] }
  | { type: 'list'; ordered: boolean; items: Inline[][] }
  | { type: 'code'; language: string; value: string };

/**
 * Schemes a link may use.
 *
 * An allowlist rather than a blocklist: `javascript:` is the one everybody
 * remembers, but `data:` carries a whole document and there is no shortage of
 * others. A link whose scheme is not here renders as plain text, so the words
 * survive and only the navigation is dropped.
 */
const SAFE_SCHEMES = ['https:', 'http:', 'mailto:'];

const BULLET = /^\s{0,3}[-*+]\s+(.*)$/;
const NUMBERED = /^\s{0,3}(\d+)[.)]\s+(.*)$/;
const HEADING = /^\s{0,3}(#{1,6})\s+(.*)$/;
const FENCE = /^\s{0,3}```(.*)$/;

/** Inline markers, longest first so `**` is never read as two `*`. */
const INLINE = [
  { open: '**', type: 'bold' as const },
  { open: '__', type: 'bold' as const },
  { open: '`', type: 'code' as const },
  { open: '*', type: 'italic' as const },
  { open: '_', type: 'italic' as const },
];

function isSafeHref(href: string): boolean {
  try {
    // A relative URL resolves against the base and is therefore http(s).
    return SAFE_SCHEMES.includes(new URL(href, 'https://example.invalid').protocol);
  } catch {
    return false;
  }
}

/** Index of the `)` closing the `(` at `open`, or -1. Handles nesting. */
function matchingParen(text: string, open: number): number {
  let depth = 0;
  for (let i = open; i < text.length; i += 1) {
    if (text[i] === '(') depth += 1;
    else if (text[i] === ')') {
      depth -= 1;
      if (depth === 0) return i;
    }
  }
  return -1;
}


function pushText(out: Inline[], value: string): void {
  if (!value) return;
  const last = out[out.length - 1];
  // Coalesce, so a rejected marker does not leave the text in fragments.
  if (last?.type === 'text') last.value += value;
  else out.push({ type: 'text', value });
}

/** Parse one line's worth of inline markers. Unmatched markers stay literal. */
export function parseInline(text: string): Inline[] {
  const out: Inline[] = [];
  let i = 0;

  while (i < text.length) {
    if (text[i] === '[') {
      const close = text.indexOf('](', i);
      // Scan for the matching paren rather than the first one: a url is
      // perfectly entitled to contain parentheses, and `javascript:alert(1)`
      // does, so stopping early would leave a stray `)` in the text.
      const end = close === -1 ? -1 : matchingParen(text, close + 1);
      if (close !== -1 && end !== -1) {
        const label = text.slice(i + 1, close);
        const href = text.slice(close + 2, end).trim();
        // A rejected scheme keeps the label: the words were the useful part,
        // and dropping them silently would hide that anything was there.
        if (isSafeHref(href)) out.push({ type: 'link', href, value: label });
        else pushText(out, label);
        i = end + 1;
        continue;
      }
    }

    const marker = INLINE.find((m) => text.startsWith(m.open, i));
    if (marker) {
      const close = text.indexOf(marker.open, i + marker.open.length);
      const value = close === -1 ? '' : text.slice(i + marker.open.length, close);
      // Emphasis binds tight to its text: markdown requires no space just
      // inside either marker. Without that rule "2 * 3 * 4" is one italic
      // span, and prices and arithmetic are full of loose asterisks.
      const tight = marker.type === 'code' ? Boolean(value) : Boolean(value) && value === value.trim();
      if (close !== -1 && tight) {
        out.push({ type: marker.type, value });
        i = close + marker.open.length;
        continue;
      }
    }

    pushText(out, text[i]);
    i += 1;
  }

  return out;
}

/** Parse a reply into blocks. Never throws: the input is a model's output. */
export function parseMarkdown(text: string): Block[] {
  const blocks: Block[] = [];
  const lines = (text ?? '').replace(/\r\n?/g, '\n').split('\n');

  let paragraph: string[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;

  const flushParagraph = () => {
    const joined = paragraph.join(' ').trim();
    paragraph = [];
    if (joined) blocks.push({ type: 'paragraph', content: parseInline(joined) });
  };
  const flushList = () => {
    if (list) blocks.push({ type: 'list', ordered: list.ordered, items: list.items.map(parseInline) });
    list = null;
  };
  const flush = () => {
    flushParagraph();
    flushList();
  };

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];

    const fence = FENCE.exec(line);
    if (fence) {
      flush();
      const body: string[] = [];
      i += 1;
      while (i < lines.length && !FENCE.test(lines[i])) {
        body.push(lines[i]);
        i += 1;
      }
      // Falling off the end closes it anyway: a reply cut short by a token
      // limit ends mid-block, and losing the content would be worse.
      blocks.push({ type: 'code', language: fence[1].trim(), value: body.join('\n') });
      continue;
    }

    if (!line.trim()) {
      flush();
      continue;
    }

    const heading = HEADING.exec(line);
    if (heading) {
      flush();
      blocks.push({
        type: 'heading',
        // The panel's own section headers are h2; a reply should not render
        // larger than the interface around it.
        level: Math.min(6, Math.max(2, heading[1].length)),
        content: parseInline(heading[2].trim()),
      });
      continue;
    }

    const bullet = BULLET.exec(line);
    const numbered = NUMBERED.exec(line);
    if (bullet || numbered) {
      flushParagraph();
      const ordered = Boolean(numbered);
      if (!list || list.ordered !== ordered) {
        flushList();
        list = { ordered, items: [] };
      }
      list.items.push((numbered ? numbered[2] : bullet![1]).trim());
      continue;
    }

    flushList();
    paragraph.push(line.trim());
  }

  flush();
  return blocks;
}

// --- rendering -------------------------------------------------------------

function renderInline(content: Inline[]): ReactNode[] {
  return content.map((span, index) => {
    const key = `${span.type}-${index}`;
    switch (span.type) {
      case 'bold':
        return <strong key={key}>{span.value}</strong>;
      case 'italic':
        return <em key={key}>{span.value}</em>;
      case 'code':
        return <code key={key} className="md__code">{span.value}</code>;
      case 'link':
        return (
          <a key={key} href={span.href} target="_blank" rel="noopener noreferrer">
            {span.value}
          </a>
        );
      default:
        return <span key={key}>{span.value}</span>;
    }
  });
}

export default function Markdown({ text }: { text: string }) {
  const blocks = parseMarkdown(text);

  return (
    <div className="md">
      {blocks.map((block, index) => {
        const key = `${block.type}-${index}`;
        switch (block.type) {
          case 'heading': {
            const Tag = `h${block.level}` as 'h2';
            return <Tag key={key} className="md__heading">{renderInline(block.content)}</Tag>;
          }
          case 'code':
            return (
              <pre key={key} className="md__pre">
                <code>{block.value}</code>
              </pre>
            );
          case 'list': {
            const Tag = block.ordered ? 'ol' : 'ul';
            return (
              <Tag key={key} className="md__list">
                {block.items.map((item, itemIndex) => (
                  <li key={itemIndex}>{renderInline(item)}</li>
                ))}
              </Tag>
            );
          }
          default:
            return <p key={key} className="md__p">{renderInline(block.content)}</p>;
        }
      })}
    </div>
  );
}
