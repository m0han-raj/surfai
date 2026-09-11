import { useCallback, useEffect, useState } from 'react';
import { Compass, MessageSquare, Bookmark, History, Settings, Trash2 } from 'lucide-react';
import type { Favourite, FavouriteDraft } from '../types/favourites';
import { api, ApiError } from '../services/api';
import { getSettings, onSettingsChanged } from '../services/storage';
import { openUrl } from '../services/messaging';
import { useAgent } from './useAgent';
import { usePageContext } from './usePageContext';
import Home from './pages/Home';
import FavouritesPage from './pages/FavouritesPage';
import HistoryPage from './pages/HistoryPage';
import SettingsPage from './pages/SettingsPage';
import InputBox from './components/InputBox';
import ConfirmationDialog from './components/ConfirmationDialog';

type View = 'home' | 'favourites' | 'history' | 'settings';

const TABS: Array<{ id: View; label: string; Icon: typeof Compass }> = [
  { id: 'home', label: 'Chat', Icon: MessageSquare },
  { id: 'favourites', label: 'Favourites', Icon: Bookmark },
  { id: 'history', label: 'History', Icon: History },
  { id: 'settings', label: 'Settings', Icon: Settings },
];

export default function App() {
  const [view, setView] = useState<View>('home');
  const [favourites, setFavourites] = useState<Favourite[]>([]);
  const [favouritesLoading, setFavouritesLoading] = useState(true);
  const [favouritesError, setFavouritesError] = useState<string | null>(null);

  const agent = useAgent();
  const page = usePageContext();

  // --- theme ------------------------------------------------------------
  useEffect(() => {
    function apply(theme: string) {
      document.documentElement.setAttribute('data-theme', theme);
    }
    void getSettings().then((settings) => apply(settings.theme));
    return onSettingsChanged((settings) => apply(settings.theme));
  }, []);

  // --- favourites -------------------------------------------------------
  const loadFavourites = useCallback(async () => {
    setFavouritesLoading(true);
    setFavouritesError(null);
    try {
      setFavourites(await api.listFavourites());
    } catch (error) {
      setFavouritesError(
        error instanceof ApiError && error.isNetwork
          ? 'Backend not reachable. Favourites are stored on the server.'
          : error instanceof Error
            ? error.message
            : 'Could not load favourites.',
      );
    } finally {
      setFavouritesLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadFavourites();
  }, [loadFavourites]);

  // A task may have created a favourite; refresh once it settles.
  useEffect(() => {
    if (!agent.running && agent.state === 'COMPLETED') {
      void loadFavourites();
    }
  }, [agent.running, agent.state, loadFavourites]);

  const handleSend = useCallback(
    async (message: string) => {
      setView('home');
      await agent.send(message);
      void page.refresh();
    },
    [agent, page],
  );

  const handleRunFavourite = useCallback(
    async (favourite: Favourite) => {
      setView('home');
      await agent.runFavourite(favourite.id, `Open my ${favourite.name} and continue`);
      void page.refresh();
    },
    [agent, page],
  );

  const handleOpenFavourite = useCallback(
    async (favourite: Favourite) => {
      await openUrl(favourite.url);
      setTimeout(() => void page.refresh(), 800);
    },
    [page],
  );

  const handleCreateFavourite = useCallback(
    async (draft: FavouriteDraft) => {
      await api.createFavourite(draft);
      await loadFavourites();
    },
    [loadFavourites],
  );

  const handleUpdateFavourite = useCallback(
    async (id: string, patch: Partial<FavouriteDraft>) => {
      await api.updateFavourite(id, patch);
      await loadFavourites();
    },
    [loadFavourites],
  );

  const handleDeleteFavourite = useCallback(
    async (favourite: Favourite) => {
      // eslint-disable-next-line no-alert
      const confirmed = window.confirm(`Delete the favourite "${favourite.name}"?`);
      if (!confirmed) return;
      await api.deleteFavourite(favourite.id);
      await loadFavourites();
    },
    [loadFavourites],
  );

  const domain = page.insight?.domain ?? hostOf(page.tab.url);

  return (
    <div className="app">
      <header className="app__header">
        <div className="app__brand">
          <span className="app__brand-mark" aria-hidden="true">
            <Compass size={14} />
          </span>
          SurfAI
        </div>

        <div className="app__header-actions">
          {view === 'home' && agent.messages.length > 0 && !agent.running && (
            <button
              type="button"
              className="button button--icon"
              onClick={agent.clear}
              aria-label="Clear conversation"
              title="Clear conversation"
            >
              <Trash2 size={14} aria-hidden="true" />
            </button>
          )}
        </div>
      </header>

      <nav className="app__nav" aria-label="Sections">
        {TABS.map(({ id, label, Icon }) => (
          <button
            key={id}
            type="button"
            className="tab"
            aria-current={view === id ? 'page' : undefined}
            onClick={() => setView(id)}
          >
            <Icon size={13} aria-hidden="true" />
            {label}
          </button>
        ))}
      </nav>

      <main className="app__main">
        {view === 'home' && (
          <Home
            messages={agent.messages}
            activity={agent.activity}
            state={agent.state}
            step={agent.step}
            maxSteps={agent.maxSteps}
            running={agent.running}
            insight={page.insight}
            pageLoading={page.loading}
            pageError={page.error}
            favourites={favourites}
            favouritesLoading={favouritesLoading}
            onRefreshPage={page.refresh}
            onRunFavourite={handleRunFavourite}
            onViewFavourites={() => setView('favourites')}
            onStop={agent.stop}
          />
        )}

        {view === 'favourites' && (
          <FavouritesPage
            favourites={favourites}
            loading={favouritesLoading}
            error={favouritesError}
            currentUrl={page.tab.url}
            currentTitle={page.tab.title}
            onRun={handleRunFavourite}
            onOpen={handleOpenFavourite}
            onCreate={handleCreateFavourite}
            onUpdate={handleUpdateFavourite}
            onDelete={handleDeleteFavourite}
          />
        )}

        {view === 'history' && <HistoryPage />}
        {view === 'settings' && <SettingsPage />}
      </main>

      {view === 'home' && (
        <InputBox onSubmit={handleSend} onStop={agent.stop} running={agent.running} />
      )}

      {agent.pendingConfirmation && (
        <ConfirmationDialog
          directive={agent.pendingConfirmation}
          domain={domain}
          onAllow={() => agent.answerConfirmation(true)}
          onCancel={() => agent.answerConfirmation(false)}
        />
      )}
    </div>
  );
}

function hostOf(url: string): string {
  try {
    return new URL(url).hostname;
  } catch {
    return '';
  }
}
