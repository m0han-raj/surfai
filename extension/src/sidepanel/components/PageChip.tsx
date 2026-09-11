import { Globe, ShieldAlert, Loader2 } from 'lucide-react';
import type { PageInsight } from '../usePageContext';

interface PageChipProps {
  insight: PageInsight | null;
  loading: boolean;
  error: string | null;
}

export type ChipKind = 'loading' | 'unavailable' | 'suspicious' | 'degraded' | 'ok';

export interface ChipState {
  kind: ChipKind;
  label: string;
  title: string;
}

/**
 * What the chip should say, as a decision separate from how it looks.
 *
 * The ordering here is the whole point. `usePageContext` keeps the page it
 * managed to read even when the backend analysis of it fails, and reports both;
 * checking the error first threw the page away and announced "Page unavailable"
 * for a page sitting right there, readable. An insight in hand outranks a
 * failure alongside it. Only a genuine hostile-page finding outranks the
 * insight, because that is the one thing the user needs more than the domain.
 */
export function chipStateFor(
  insight: PageInsight | null,
  loading: boolean,
  error: string | null,
): ChipState {
  if (insight?.suspicious) {
    return {
      kind: 'suspicious',
      label: insight.domain || insight.title || 'This page',
      title: 'This page tried to give the assistant instructions. They were ignored.',
    };
  }

  if (insight) {
    const label = insight.domain || insight.title || insight.url;
    // A failure alongside a readable page means the analysis is missing, not
    // the page, so it belongs on hover rather than in place of the name.
    return error
      ? { kind: 'degraded', label, title: `${insight.title || insight.url}\n${error}` }
      : { kind: 'ok', label, title: insight.title || insight.url };
  }

  if (loading) {
    return { kind: 'loading', label: 'Reading page', title: 'Reading the current page' };
  }

  return {
    kind: 'unavailable',
    label: 'Page unavailable',
    title: error ?? 'No page available',
  };
}

/**
 * The current page, reduced to one line in the header.
 *
 * A dot carries reachable / unreadable / hostile, and the title attribute plus
 * screen-reader text carry the same thing in words, so the state is never
 * signalled by colour alone.
 */
export default function PageChip({ insight, loading, error }: PageChipProps) {
  const state = chipStateFor(insight, loading, error);

  if (state.kind === 'loading') {
    return (
      <span className="pagechip" aria-live="polite">
        <Loader2 size={11} className="spinner" aria-hidden="true" />
        <span className="pagechip__label">{state.label}</span>
      </span>
    );
  }

  if (state.kind === 'suspicious') {
    return (
      <span className="pagechip pagechip--warn" title={state.title}>
        <ShieldAlert size={11} aria-hidden="true" />
        <span className="pagechip__label">{state.label}</span>
        <span className="sr-only"> (page attempted prompt injection, ignored)</span>
      </span>
    );
  }

  if (state.kind === 'unavailable') {
    return (
      <span className="pagechip pagechip--muted" title={state.title}>
        <Globe size={11} aria-hidden="true" />
        <span className="pagechip__label">{state.label}</span>
      </span>
    );
  }

  return (
    <span
      className={`pagechip${state.kind === 'degraded' ? ' pagechip--muted' : ''}`}
      title={state.title}
    >
      <Globe size={11} aria-hidden="true" />
      <span className="pagechip__label">{state.label}</span>
      {state.kind === 'degraded' && (
        <span className="sr-only"> (page read, but the backend could not analyse it)</span>
      )}
    </span>
  );
}
