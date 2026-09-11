import type { Favourite } from '../../types/favourites';
import type { ActivityEntry, AgentState, ChatMessage } from '../../types/agent';
import type { PageInsight } from '../usePageContext';
import Chat from '../components/Chat';
import CurrentPage from '../components/CurrentPage';
import AgentStatus from '../components/AgentStatus';
import Favourites from '../components/Favourites';

interface HomeProps {
  messages: ChatMessage[];
  activity: ActivityEntry[];
  state: AgentState;
  step: number;
  maxSteps: number;
  running: boolean;
  insight: PageInsight | null;
  pageLoading: boolean;
  pageError: string | null;
  favourites: Favourite[];
  favouritesLoading: boolean;
  onRefreshPage: () => void;
  onRunFavourite: (favourite: Favourite) => void;
  onViewFavourites: () => void;
  onStop: () => void;
}

/**
 * The main panel: current page, live agent activity, conversation, favourites.
 *
 * Ordered by what the user needs at a glance -- where am I, what is the agent
 * doing, what did it say, what can I re-run.
 */
export default function Home({
  messages,
  activity,
  state,
  step,
  maxSteps,
  running,
  insight,
  pageLoading,
  pageError,
  favourites,
  favouritesLoading,
  onRefreshPage,
  onRunFavourite,
  onViewFavourites,
  onStop,
}: HomeProps) {
  return (
    <>
      <CurrentPage
        insight={insight}
        loading={pageLoading}
        error={pageError}
        onRefresh={onRefreshPage}
      />

      <AgentStatus
        entries={activity}
        state={state}
        step={step}
        maxSteps={maxSteps}
        running={running}
        onStop={onStop}
      />

      <Chat messages={messages} />

      {!running && (
        <Favourites
          favourites={favourites}
          loading={favouritesLoading}
          onRun={onRunFavourite}
          onViewAll={onViewFavourites}
        />
      )}
    </>
  );
}
