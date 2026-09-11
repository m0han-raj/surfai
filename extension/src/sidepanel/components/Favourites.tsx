import { Bookmark } from 'lucide-react';
import type { Favourite } from '../../types/favourites';
import FavouriteCard from './FavouriteCard';

interface FavouritesProps {
  favourites: Favourite[];
  loading: boolean;
  onRun: (favourite: Favourite) => void;
  onViewAll: () => void;
  limit?: number;
}

/** Compact favourites list for the Home panel. */
export default function Favourites({
  favourites,
  loading,
  onRun,
  onViewAll,
  limit = 4,
}: FavouritesProps) {
  return (
    <section className="section" aria-label="Favourites">
      <h2 className="section__header section__header--static">
        <span>Favourites</span>
        {favourites.length > limit && (
          <button type="button" className="button button--ghost button--sm" onClick={onViewAll}>
            View all ({favourites.length})
          </button>
        )}
      </h2>

      <div className="section__body">
        {loading && <div className="skeleton" style={{ height: 44 }} />}

        {!loading && favourites.length === 0 && (
          <p className="text-sm text-muted">
            <Bookmark size={12} aria-hidden="true" style={{ verticalAlign: -1 }} /> No favourites
            yet. Say &ldquo;save this as my ...&rdquo; on a page you use often.
          </p>
        )}

        {!loading &&
          favourites
            .slice(0, limit)
            .map((favourite) => (
              <FavouriteCard key={favourite.id} favourite={favourite} onRun={onRun} compact />
            ))}
      </div>
    </section>
  );
}
