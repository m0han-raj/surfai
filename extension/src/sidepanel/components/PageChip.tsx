import { Globe, ShieldAlert, Loader2 } from 'lucide-react';
import type { PageInsight } from '../usePageContext';

interface PageChipProps {
  insight: PageInsight | null;
  loading: boolean;
  error: string | null;
}

/**
 * The current page, reduced to one line in the header.
 *
 * A dot carries reachable / unreadable / hostile, and the title attribute plus
 * screen-reader text carry the same thing in words, so the state is never
 * signalled by colour alone.
 */
export default function PageChip({ insight, loading, error }: PageChipProps) {
  if (loading && !insight) {
    return (
      <span className="pagechip" aria-live="polite">
        <Loader2 size={11} className="spinner" aria-hidden="true" />
        <span className="pagechip__label">Reading page</span>
      </span>
    );
  }

  if (error || !insight) {
    return (
      <span className="pagechip pagechip--muted" title={error ?? 'No page available'}>
        <Globe size={11} aria-hidden="true" />
        <span className="pagechip__label">Page unavailable</span>
      </span>
    );
  }

  if (insight.suspicious) {
    return (
      <span
        className="pagechip pagechip--warn"
        title="This page tried to give the assistant instructions. They were ignored."
      >
        <ShieldAlert size={11} aria-hidden="true" />
        <span className="pagechip__label">{insight.domain}</span>
        <span className="sr-only"> (page attempted prompt injection, ignored)</span>
      </span>
    );
  }

  return (
    <span className="pagechip" title={insight.title || insight.url}>
      <Globe size={11} aria-hidden="true" />
      <span className="pagechip__label">{insight.domain}</span>
    </span>
  );
}
