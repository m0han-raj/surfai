import { Play, Pencil, Trash2, ExternalLink } from 'lucide-react';
import type { Favourite } from '../../types/favourites';

interface FavouriteCardProps {
  favourite: Favourite;
  onRun: (favourite: Favourite) => void;
  onEdit?: (favourite: Favourite) => void;
  onDelete?: (favourite: Favourite) => void;
  onOpen?: (favourite: Favourite) => void;
  compact?: boolean;
}

/**
 * An AI-aware favourite.
 *
 * Shows the *intent* and preferences, not just the URL -- that is the whole
 * difference between this and a bookmark, so it is what the card leads with.
 */
export default function FavouriteCard({
  favourite,
  onRun,
  onEdit,
  onDelete,
  onOpen,
  compact = false,
}: FavouriteCardProps) {
  const preferences = Object.entries(favourite.preferences ?? {}).slice(0, 4);

  return (
    <div className="favourite">
      <button
        type="button"
        className="favourite__main"
        onClick={() => onRun(favourite)}
        aria-label={`Run saved task: ${favourite.name}`}
      >
        <div className="favourite__name">{favourite.name}</div>
        <div className="favourite__domain">{favourite.domain}</div>

        {!compact && favourite.intent && (
          <p className="favourite__intent">{favourite.intent}</p>
        )}

        {!compact && preferences.length > 0 && (
          <div className="favourite__prefs">
            {preferences.map(([key, value]) => (
              <span key={key} className="chip">
                {key}: {formatValue(value)}
              </span>
            ))}
          </div>
        )}
      </button>

      <div className="favourite__actions">
        <button
          type="button"
          className="button button--icon"
          onClick={() => onRun(favourite)}
          aria-label={`Run ${favourite.name}`}
          title="Run"
        >
          <Play size={13} aria-hidden="true" />
        </button>

        {onOpen && (
          <button
            type="button"
            className="button button--icon"
            onClick={() => onOpen(favourite)}
            aria-label={`Open ${favourite.name} in this tab`}
            title="Open page"
          >
            <ExternalLink size={13} aria-hidden="true" />
          </button>
        )}

        {onEdit && (
          <button
            type="button"
            className="button button--icon"
            onClick={() => onEdit(favourite)}
            aria-label={`Edit ${favourite.name}`}
            title="Edit"
          >
            <Pencil size={13} aria-hidden="true" />
          </button>
        )}

        {onDelete && (
          <button
            type="button"
            className="button button--icon"
            onClick={() => onDelete(favourite)}
            aria-label={`Delete ${favourite.name}`}
            title="Delete"
          >
            <Trash2 size={13} aria-hidden="true" />
          </button>
        )}
      </div>
    </div>
  );
}

function formatValue(value: unknown): string {
  if (Array.isArray(value)) return value.slice(0, 3).join(', ');
  if (value && typeof value === 'object') return JSON.stringify(value).slice(0, 40);
  return String(value).slice(0, 40);
}
