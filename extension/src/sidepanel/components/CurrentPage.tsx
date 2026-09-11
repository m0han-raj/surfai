import { Globe, ShieldAlert, RefreshCw } from 'lucide-react';
import type { PageInsight } from '../usePageContext';

interface CurrentPageProps {
  insight: PageInsight | null;
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
}

const CAPABILITY_LABELS: Record<string, string> = {
  search: 'Search',
  filters: 'Filters',
  pagination: 'Pagination',
  results: 'Results',
  auth: 'Sign-in',
};

/**
 * The Current Page card.
 *
 * Shows what SurfAI can actually see and do here *before* the user asks for
 * anything. On a page it cannot read, saying so up front is far better than
 * failing once a task is already running.
 */
export default function CurrentPage({ insight, loading, error, onRefresh }: CurrentPageProps) {
  return (
    <section className="section" aria-label="Current page">
      <h2 className="section__header section__header--static">
        <span>Current Page</span>
        <button
          type="button"
          className="button button--icon"
          onClick={onRefresh}
          aria-label="Refresh page analysis"
          title="Refresh"
          disabled={loading}
        >
          <RefreshCw size={12} className={loading ? 'spinner' : undefined} aria-hidden="true" />
        </button>
      </h2>

      <div className="section__body">
        {error && (
          <div className="banner banner--warning" role="status" style={{ margin: 0 }}>
            <ShieldAlert size={14} aria-hidden="true" />
            <span>{error}</span>
          </div>
        )}

        {!error && loading && !insight && <div className="skeleton" style={{ height: 48 }} />}

        {!error && insight && (
          <div className="page-card">
            <div className="page-card__domain">
              <Globe size={12} aria-hidden="true" />
              {insight.domain || 'unknown site'}
            </div>
            <div className="page-card__title" title={insight.title}>
              {insight.title || 'Untitled page'}
            </div>

            <div className="page-card__meta">
              <span className="chip">{insight.elementCount} elements</span>
              {Object.entries(insight.capabilities)
                .filter(([, enabled]) => enabled)
                .map(([key]) => (
                  <span key={key} className="chip chip--accent">
                    {CAPABILITY_LABELS[key] ?? key}
                  </span>
                ))}
            </div>

            {insight.suspicious && (
              <div className="banner banner--warning" role="alert" style={{ margin: '8px 0 0' }}>
                <ShieldAlert size={14} aria-hidden="true" />
                <span>
                  This page contains text that tries to instruct an AI assistant. SurfAI treats
                  it as data and will ignore it.
                </span>
              </div>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
