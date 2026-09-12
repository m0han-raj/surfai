import { useState } from 'react';
import { ImageOff, ChevronDown } from 'lucide-react';
import type { ResultItem } from '../../types/agent';

/**
 * The results SurfAI found, as cards.
 *
 * One shape for every kind of list. A shopping result, a job posting, a news
 * item and a docs hit differ in which fields they happen to carry, not in
 * what they are, so the card shows what is there and omits what is not rather
 * than reserving space for a price that will never come.
 *
 * Every field was copied out of the page. Nothing here was written by a model,
 * which is the point: a price shown beside a product has to be the price the
 * page printed.
 */

interface ResultCardsProps {
  items: ResultItem[];
}

/**
 * Cards shown before asking for the rest.
 *
 * Twelve in a 400px panel is a wall you scroll past rather than an answer you
 * read. The backend already sent the whole selection, so the rest are held
 * here and cost nothing to reveal.
 */
export const VISIBLE_BY_DEFAULT = 5;

export function splitResults(
  items: ResultItem[],
  expanded: boolean,
): { visible: ResultItem[]; hidden: number } {
  if (expanded || items.length <= VISIBLE_BY_DEFAULT) {
    return { visible: items, hidden: 0 };
  }
  // Sliced, never sorted: the model ranked these best-first, so the first five
  // are the five it would have named.
  return {
    visible: items.slice(0, VISIBLE_BY_DEFAULT),
    hidden: items.length - VISIBLE_BY_DEFAULT,
  };
}

function Thumbnail({ src, alt }: { src?: string | null; alt: string }) {
  const [failed, setFailed] = useState(false);

  // A page's images are hotlinked from wherever that page serves them, and a
  // fair number will not load: expired urls, hotlink protection, a CDN that
  // wants a referrer. A broken-image icon in every card looks like our bug, so
  // a failure becomes a quiet placeholder that keeps the layout intact.
  if (!src || failed) {
    return (
      <div className="card__thumb card__thumb--empty" aria-hidden="true">
        <ImageOff size={14} />
      </div>
    );
  }

  return (
    <img
      className="card__thumb"
      src={src}
      alt={alt}
      loading="lazy"
      referrerPolicy="no-referrer"
      onError={() => setFailed(true)}
    />
  );
}

function Card({ item }: { item: ResultItem }) {
  const body = (
    <>
      <Thumbnail src={item.image} alt="" />
      <div className="card__body">
        <span className="card__title">{item.title}</span>
        {item.price && <span className="card__price">{item.price}</span>}
        {item.meta?.length ? (
          <span className="card__meta">{item.meta.join(' · ')}</span>
        ) : null}
      </div>
    </>
  );

  // Links arrive from page content and were scheme-checked on the way here.
  // An item without one is still worth showing; it just is not clickable.
  if (!item.url) return <li className="card">{body}</li>;

  return (
    <li className="card card--link">
      <a href={item.url} target="_blank" rel="noopener noreferrer" title={item.title}>
        {body}
      </a>
    </li>
  );
}

export default function ResultCards({ items }: ResultCardsProps) {
  const [expanded, setExpanded] = useState(false);
  if (!items.length) return null;

  const { visible, hidden } = splitResults(items, expanded);

  return (
    <>
      <ul className="cards" aria-label={`${items.length} results`}>
        {visible.map((item, index) => (
          <Card key={`${item.url ?? item.title}-${index}`} item={item} />
        ))}
      </ul>

      {hidden > 0 && (
        <button type="button" className="cards__more" onClick={() => setExpanded(true)}>
          <ChevronDown size={12} aria-hidden="true" />
          Show {hidden} more
        </button>
      )}
    </>
  );
}
