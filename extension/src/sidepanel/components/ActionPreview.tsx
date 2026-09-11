import type { BrowserAction } from '../../types/actions';

interface ActionPreviewProps {
  action: BrowserAction;
}

/** Human-readable description of a pending action. */
export function describeAction(action: BrowserAction): string {
  switch (action.action) {
    case 'CLICK':
      return 'Click an element on the page';
    case 'TYPE':
      return `Type "${truncate(action.value ?? '', 60)}" into a field`;
    case 'SELECT':
      return `Choose "${truncate(action.value ?? '', 60)}" from a dropdown`;
    case 'SCROLL':
      return `Scroll ${action.direction ?? 'down'}`;
    case 'NAVIGATE':
      return `Open ${truncate(action.value ?? '', 80)}`;
    case 'EXTRACT':
      return 'Read information from the page';
    case 'WAIT':
      return `Wait ${action.timeout_ms ?? 1000}ms`;
    default:
      return 'Perform an action';
  }
}

function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max)}...` : text;
}

/** Compact, monospaced rendering of exactly what will be executed. */
export default function ActionPreview({ action }: ActionPreviewProps) {
  const detail = action.value ?? action.target ?? action.direction ?? '';

  return (
    <div className="action-preview">
      <span className="action-preview__verb">{action.action}</span>
      {detail && <span className="action-preview__target">{truncate(String(detail), 48)}</span>}
    </div>
  );
}
