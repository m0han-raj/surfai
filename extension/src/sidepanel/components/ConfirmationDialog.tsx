import { useEffect, useRef } from 'react';
import { AlertTriangle, ShieldAlert, Info } from 'lucide-react';
import type { Directive } from '../../types/agent';
import ActionPreview, { describeAction } from './ActionPreview';

interface ConfirmationDialogProps {
  directive: Directive;
  domain: string;
  onAllow: () => void;
  onCancel: () => void;
}

/**
 * Human-in-the-loop gate for medium and high risk actions.
 *
 * The dialog states plainly what will happen, on which site, and why it needs
 * approval -- the user should never have to guess what they are authorising.
 * Cancel is focused on open and Escape cancels, so the safe choice is the
 * default one.
 */
export default function ConfirmationDialog({
  directive,
  domain,
  onAllow,
  onCancel,
}: ConfirmationDialogProps) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);

  const level = directive.risk?.level ?? 'MEDIUM';
  const action = directive.action;

  useEffect(() => {
    cancelRef.current?.focus();
  }, []);

  // Escape cancels, and focus stays inside the dialog while it is open.
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        event.preventDefault();
        onCancel();
        return;
      }
      if (event.key !== 'Tab' || !dialogRef.current) return;

      const focusable = dialogRef.current.querySelectorAll<HTMLElement>(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
      );
      if (focusable.length === 0) return;

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [onCancel]);

  const RiskIcon = level === 'HIGH' ? ShieldAlert : level === 'MEDIUM' ? AlertTriangle : Info;

  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={(e) => {
      if (e.target === e.currentTarget) onCancel();
    }}>
      <div
        ref={dialogRef}
        className="dialog"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        aria-describedby="confirm-description"
      >
        <h2 className="dialog__title" id="confirm-title">
          <RiskIcon size={16} aria-hidden="true" />
          Action requires confirmation
        </h2>

        <div className={`risk-banner risk-banner--${level}`} id="confirm-description">
          <RiskIcon size={14} aria-hidden="true" />
          <span>
            {/* Risk level is spelled out, not signalled by colour alone. */}
            <strong>{level === 'HIGH' ? 'High risk.' : level === 'MEDIUM' ? 'Needs review.' : 'Note.'}</strong>{' '}
            {directive.risk?.explanation ?? 'This action changes something on the page.'}
          </span>
        </div>

        <div className="dialog__section">
          <div className="dialog__label">The agent wants to</div>
          <div className="dialog__value">
            {action ? describeAction(action) : directive.message}
          </div>
        </div>

        <div className="dialog__section">
          <div className="dialog__label">Website</div>
          <div className="dialog__value">{domain || 'this page'}</div>
        </div>

        {action && (
          <div className="dialog__section">
            <div className="dialog__label">Exact action</div>
            <ActionPreview action={action} />
          </div>
        )}

        <div className="dialog__actions">
          <button ref={cancelRef} type="button" className="button" onClick={onCancel}>
            Cancel
          </button>
          <button
            type="button"
            className={level === 'HIGH' ? 'button button--danger' : 'button button--primary'}
            onClick={onAllow}
          >
            Allow
          </button>
        </div>
      </div>
    </div>
  );
}
