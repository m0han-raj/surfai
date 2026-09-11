import type { ChatMessage } from '../../types/agent';
import type { PageInsight } from '../usePageContext';
import Chat from '../components/Chat';

interface HomeProps {
  messages: ChatMessage[];
  insight: PageInsight | null;
  onPickSuggestion: (prompt: string) => void;
}

/**
 * The main view: just the conversation.
 *
 * Page context lives in the header, agent steps fold under the reply that used
 * them, and favourites and history have their own tabs. Nothing sits between
 * the user and the thread.
 */
export default function Home({ messages, insight, onPickSuggestion }: HomeProps) {
  return <Chat messages={messages} insight={insight} onPickSuggestion={onPickSuggestion} />;
}
