import { Check, CircleDashed, Loader2, Info, X } from 'lucide-react';
import type { ActivityEntry, AgentState } from '../../types/agent';

interface AgentStatusProps {
  entries: ActivityEntry[];
  state: AgentState;
  step: number;
  maxSteps: number;
  running: boolean;
  onStop: () => void;
}

/**
 * The Agent Activity log.
 *
 * Shows *what SurfAI did*, never how it reasoned: each line is a concrete,
 * externally observable step. Chain-of-thought is deliberately not surfaced.
 */
export default function AgentStatus({
  entries,
  state,
  step,
  maxSteps,
  running,
  onStop,
}: AgentStatusProps) {
  const current = entries.find((entry) => entry.status === 'running');

  if (!running && entries.length === 0) return null;

  return (
    <section className="section" aria-label="Agent activity">
      <h2 className="section__header section__header--static">
        <span>Agent Activity</span>
        {maxSteps > 0 && (
          <span className="section__count">
            Step {step} of {maxSteps}
          </span>
        )}
      </h2>

      <div className="section__body">
        {running && (
          <div className="running-bar">
            <Loader2 size={14} className="spinner" aria-hidden="true" />
            <span className="running-bar__label">{current?.label ?? 'Working'}</span>
            <button type="button" className="button button--danger button--sm" onClick={onStop}>
              <X size={12} aria-hidden="true" />
              Stop
            </button>
          </div>
        )}

        <ol className="activity" aria-live="polite" aria-atomic="false">
          {entries.slice(-8).map((entry) => (
            <li key={entry.id} className={`activity__row activity__row--${entry.status}`}>
              <span className="activity__icon">
                {entry.status === 'running' && (
                  <Loader2 size={12} className="spinner" aria-hidden="true" />
                )}
                {entry.status === 'done' && <Check size={12} aria-hidden="true" />}
                {entry.status === 'failed' && <X size={12} aria-hidden="true" />}
                {entry.status === 'info' && <Info size={12} aria-hidden="true" />}
              </span>
              <span>{entry.label}</span>
              {/* Status is conveyed in text too, never by colour alone. */}
              <span className="sr-only">{` (${entry.status})`}</span>
            </li>
          ))}
        </ol>

        {!running && entries.length > 0 && (
          <p className="text-xs text-muted" style={{ marginTop: 'var(--space-2)' }}>
            <CircleDashed size={11} aria-hidden="true" style={{ verticalAlign: -1 }} /> Agent state:{' '}
            {state}
          </p>
        )}
      </div>
    </section>
  );
}
