import { useState } from 'react';
import { ChevronRight, ShieldAlert, AlertCircle, Loader2 } from 'lucide-react';
import type { ChatMessage } from '../../types/agent';

interface MessageProps {
  message: ChatMessage;
}

/**
 * One turn in the conversation.
 *
 * When SurfAI acted on the page, the steps it took collapse into a single line
 * under the reply rather than living in a separate activity panel. A plain
 * answer shows no steps at all, which is what keeps the transcript reading as a
 * conversation instead of a task log.
 */
export default function Message({ message }: MessageProps) {
  const [stepsOpen, setStepsOpen] = useState(false);
  const isUser = message.role === 'user';
  const steps = message.steps ?? [];

  if (isUser) {
    return (
      <article className="msg msg--user" aria-label="Your message">
        <div className="msg__text">{message.content}</div>
      </article>
    );
  }

  return (
    <article
      className={`msg msg--assistant${message.error ? ' msg--error' : ''}`}
      aria-label="SurfAI response"
    >
      {message.pending ? (
        <div className="msg__pending" role="status">
          <Loader2 size={13} className="spinner" aria-hidden="true" />
          <span>{message.content || 'Thinking'}</span>
        </div>
      ) : (
        <div className="msg__text">
          {message.error && (
            <AlertCircle size={13} aria-hidden="true" className="msg__error-icon" />
          )}
          {message.content}
        </div>
      )}

      {steps.length > 0 && (
        <div className="steps">
          <button
            type="button"
            className="steps__toggle"
            aria-expanded={stepsOpen}
            onClick={() => setStepsOpen((open) => !open)}
          >
            <ChevronRight
              size={12}
              aria-hidden="true"
              className={stepsOpen ? 'steps__chev steps__chev--open' : 'steps__chev'}
            />
            {steps.length} {steps.length === 1 ? 'step' : 'steps'}
          </button>

          {stepsOpen && (
            <ol className="steps__list">
              {steps.map((step) => (
                <li key={step.id} className={`steps__item steps__item--${step.status}`}>
                  {step.label}
                  {/* Status is in text too, never conveyed by colour alone. */}
                  {step.status === 'failed' && <span className="sr-only"> (failed)</span>}
                </li>
              ))}
            </ol>
          )}
        </div>
      )}

      {message.warnings?.map((warning) => (
        <div key={warning} className="msg__notice" role="note">
          <ShieldAlert size={13} aria-hidden="true" />
          <span>{warning}</span>
        </div>
      ))}
    </article>
  );
}
