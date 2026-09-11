import { useState } from 'react';
import { Plus, X, Save } from 'lucide-react';
import type { Favourite, FavouriteDraft } from '../../types/favourites';
import FavouriteCard from '../components/FavouriteCard';

interface FavouritesPageProps {
  favourites: Favourite[];
  loading: boolean;
  error: string | null;
  currentUrl: string;
  currentTitle: string;
  onRun: (favourite: Favourite) => void;
  onOpen: (favourite: Favourite) => void;
  onCreate: (draft: FavouriteDraft) => Promise<void>;
  onUpdate: (id: string, patch: Partial<FavouriteDraft>) => Promise<void>;
  onDelete: (favourite: Favourite) => Promise<void>;
}

/** Full CRUD over AI-aware favourites (AC-09). */
export default function FavouritesPage({
  favourites,
  loading,
  error,
  currentUrl,
  currentTitle,
  onRun,
  onOpen,
  onCreate,
  onUpdate,
  onDelete,
}: FavouritesPageProps) {
  const [editing, setEditing] = useState<Favourite | 'new' | null>(null);

  if (editing) {
    return (
      <FavouriteEditor
        favourite={editing === 'new' ? null : editing}
        currentUrl={currentUrl}
        currentTitle={currentTitle}
        onCancel={() => setEditing(null)}
        onSave={async (draft) => {
          if (editing === 'new') {
            await onCreate(draft);
          } else {
            await onUpdate(editing.id, draft);
          }
          setEditing(null);
        }}
      />
    );
  }

  return (
    <>
      <section className="section">
        <h2 className="section__header section__header--static">
          <span>Favourites</span>
          <button
            type="button"
            className="button button--ghost button--sm"
            onClick={() => setEditing('new')}
          >
            <Plus size={12} aria-hidden="true" />
            New
          </button>
        </h2>

        <div className="section__body">
          {error && (
            <div className="banner banner--danger" role="alert" style={{ margin: 0 }}>
              <X size={14} aria-hidden="true" />
              <span>{error}</span>
            </div>
          )}

          {loading && <div className="skeleton" style={{ height: 72 }} />}

          {!loading && !error && favourites.length === 0 && (
            <div className="empty">
              <p className="empty__title">No favourites yet</p>
              <p className="empty__body">
                A SurfAI favourite stores why you visit a page, not just its address, so you can
                say &ldquo;check my AI jobs&rdquo; later and it knows what to look for.
              </p>
            </div>
          )}

          {!loading &&
            favourites.map((favourite) => (
              <FavouriteCard
                key={favourite.id}
                favourite={favourite}
                onRun={onRun}
                onOpen={onOpen}
                onEdit={setEditing}
                onDelete={onDelete}
              />
            ))}
        </div>
      </section>
    </>
  );
}

interface EditorProps {
  favourite: Favourite | null;
  currentUrl: string;
  currentTitle: string;
  onSave: (draft: FavouriteDraft) => Promise<void>;
  onCancel: () => void;
}

function FavouriteEditor({
  favourite,
  currentUrl,
  currentTitle,
  onSave,
  onCancel,
}: EditorProps) {
  const [name, setName] = useState(favourite?.name ?? currentTitle.slice(0, 60));
  const [url, setUrl] = useState(favourite?.url ?? currentUrl);
  const [intent, setIntent] = useState(favourite?.intent ?? '');
  const [description, setDescription] = useState(favourite?.description ?? '');
  const [preferences, setPreferences] = useState(
    JSON.stringify(favourite?.preferences ?? {}, null, 2),
  );
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setFormError(null);

    if (!name.trim()) {
      setFormError('Give the favourite a name you would say out loud, such as "AI Jobs".');
      return;
    }
    if (!url.trim()) {
      setFormError('A favourite needs a URL.');
      return;
    }

    let parsedPreferences: Record<string, unknown> = {};
    try {
      parsedPreferences = preferences.trim() ? JSON.parse(preferences) : {};
    } catch {
      setFormError('Preferences must be valid JSON, for example {"location": "India"}.');
      return;
    }

    setSaving(true);
    try {
      await onSave({
        name: name.trim(),
        url: url.trim(),
        intent: intent.trim(),
        description: description.trim(),
        preferences: parsedPreferences,
      });
    } catch (error) {
      setFormError(error instanceof Error ? error.message : 'Could not save the favourite.');
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="section">
      <h2 className="section__header section__header--static">
        <span>{favourite ? 'Edit favourite' : 'New favourite'}</span>
      </h2>

      <form className="section__body" onSubmit={submit}>
        {formError && (
          <div className="banner banner--danger" role="alert" style={{ margin: '0 0 12px' }}>
            <X size={14} aria-hidden="true" />
            <span>{formError}</span>
          </div>
        )}

        <div className="field">
          <label className="field__label" htmlFor="fav-name">
            Name
          </label>
          <input
            id="fav-name"
            className="input"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="AI Jobs"
            required
          />
          <p className="field__hint">What you would call it out loud: &ldquo;open my AI jobs&rdquo;.</p>
        </div>

        <div className="field">
          <label className="field__label" htmlFor="fav-url">
            URL
          </label>
          <input
            id="fav-url"
            className="input"
            type="url"
            value={url}
            onChange={(event) => setUrl(event.target.value)}
            placeholder="https://example.com/jobs"
            required
          />
        </div>

        <div className="field">
          <label className="field__label" htmlFor="fav-intent">
            Intent
          </label>
          <textarea
            id="fav-intent"
            className="textarea"
            value={intent}
            onChange={(event) => setIntent(event.target.value)}
            placeholder="Find entry-level AI/ML jobs"
          />
          <p className="field__hint">
            What you are trying to achieve. SurfAI uses this to run the task later.
          </p>
        </div>

        <div className="field">
          <label className="field__label" htmlFor="fav-description">
            Description
          </label>
          <input
            id="fav-description"
            className="input"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            placeholder="Jobs relevant to my early-career AI/ML search"
          />
        </div>

        <div className="field">
          <label className="field__label" htmlFor="fav-preferences">
            Preferences
          </label>
          <textarea
            id="fav-preferences"
            className="textarea mono"
            value={preferences}
            onChange={(event) => setPreferences(event.target.value)}
            spellCheck={false}
            placeholder={'{\n  "location": "India",\n  "experience": "0-2 years"\n}'}
          />
          <p className="field__hint">
            JSON key/value pairs that constrain the search, such as location or budget.
          </p>
        </div>

        <div className="row" style={{ justifyContent: 'flex-end' }}>
          <button type="button" className="button" onClick={onCancel} disabled={saving}>
            Cancel
          </button>
          <button type="submit" className="button button--primary" disabled={saving}>
            <Save size={12} aria-hidden="true" />
            {saving ? 'Saving' : 'Save'}
          </button>
        </div>
      </form>
    </section>
  );
}
