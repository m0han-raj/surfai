import { useCallback, useEffect, useState } from 'react';
import { MessageSquare, Bookmark, History, Settings, PenSquare } from 'lucide-react';
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
import PageChip from './components/PageChip';

type View = 'chat' | 'favourites' | 'history' | 'settings';

const TABS: Array<{ id: View; label: string; Icon: typeof MessageSquare }> = [
  { id: 'chat', label: 'Chat', Icon: MessageSquare },
  { id: 'favourites', label: 'Saved', Icon: Bookmark },
  { id: 'history', label: 'History', Icon: History },
  { id: 'settings', label: 'Settings', Icon: Settings },
];

export default function App() {
  const [view, setView] = useState<View>('chat');
  const [favourites, setFavourites] = useState<Favourite[]>([]);
  const [favouritesLoading, setFavouritesLoading] = useState(true);
  const [favouritesError, setFavouritesError] = useState<string | null>(null);

  const agent = useAgent();
  const page = usePageContext();

  useEffect(() => {
    function apply(theme: string) {
      document.documentElement.setAttribute('data-theme', theme);
    }
    void getSettings().then((settings) => apply(settings.theme));
    return onSettingsChanged((settings) => apply(settings.theme));
  }, []);

  const loadFavourites = useCallback(async () => {
    setFavouritesLoading(true);
    setFavouritesError(null);
    try {
      setFavourites(await api.listFavourites());
    } catch (error) {
      setFavouritesError(
        error instanceof ApiError && error.isNetwork
          ? 'Backend not reachable. Saved pages are stored on the server.'
          : error instanceof Error
            ? error.message
            : 'Could not load saved pages.',
      );
    } finally {
      setFavouritesLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadFavourites();
  }, [loadFavourites]);

  // A reply may have saved a page; refresh once the turn settles.
  useEffect(() => {
    if (!agent.running && agent.state === 'COMPLETED') {
      void loadFavourites();
    }
  }, [agent.running, agent.state, loadFavourites]);

  const handleSend = useCallback(
    async (message: string) => {
      setView('chat');
      // The domain the question is being asked about, so a later switch knows
      // what it is a switch away from.
      await agent.send(message, page.insight?.domain ?? hostOf(page.tab.url));
      void page.refresh();
    },
    [agent, page],
  );

  // Follow the active tab. usePageContext already re-reads on tab switch and
  // navigation; this turns that into something the conversation says out loud.
  const currentDomain = page.insight?.domain ?? hostOf(page.tab.url);
  const { noteCurrentPage } = agent;
  useEffect(() => {
    // While a read is in flight the insight still describes the page we are
    // leaving, so hold off rather than announce a domain that is already
    // stale. `agent` itself is a fresh object every render; the callback is
    // the stable half of it.
    noteCurrentPage(page.loading ? undefined : currentDomain);
  }, [noteCurrentPage, currentDomain, page.loading]);

  const handleRunFavourite = useCallback(
    async (favourite: Favourite) => {
      setView('chat');
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
      if (!window.confirm(`Delete "${favourite.name}"?`)) return;
      await api.deleteFavourite(favourite.id);
      await loadFavourites();
    },
    [loadFavourites],
  );

  const domain = page.insight?.domain ?? hostOf(page.tab.url);

  return (
    <div className="app">
      <header className="app__header">
        <span className="app__brand">SurfAI</span>

        <div className="app__header-right">
          {view === 'chat' && <PageChip insight={page.insight} loading={page.loading} error={page.error} />}
          {view === 'chat' && agent.messages.length > 0 && !agent.running && (
            <button
              type="button"
              className="button button--icon"
              onClick={agent.clear}
              aria-label="Start a new conversation"
              title="New conversation"
            >
              <PenSquare size={14} aria-hidden="true" />
            </button>
          )}
        </div>
      </header>

      <main className="app__main">
        {view === 'chat' && (
          <Home
            messages={agent.messages}
            insight={page.insight}
            onPickSuggestion={handleSend}
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

      {view === 'chat' && (
        <InputBox onSubmit={handleSend} onStop={agent.stop} running={agent.running} />
      )}

      <nav className="app__nav" aria-label="Sections">
        {TABS.map(({ id, label, Icon }) => (
          <button
            key={id}
            type="button"
            className="tab"
            aria-current={view === id ? 'page' : undefined}
            onClick={() => setView(id)}
          >
            <Icon size={15} aria-hidden="true" />
            <span>{label}</span>
          </button>
        ))}
      </nav>

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
