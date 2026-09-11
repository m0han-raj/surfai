import { useEffect, useRef } from 'react';
import type { ChatMessage } from '../../types/agent';
import type { PageInsight } from '../usePageContext';
import Message from './Message';
import Suggestions from './Suggestions';

interface ChatProps {
  messages: ChatMessage[];
  insight: PageInsight | null;
  onPickSuggestion: (prompt: string) => void;
}

/** The conversation. It fills the panel; nothing else competes with it. */
export default function Chat({ messages, insight, onPickSuggestion }: ChatProps) {
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'end', behavior: 'smooth' });
  }, [messages.length, messages[messages.length - 1]?.content]);

  if (messages.length === 0) {
    return <Suggestions insight={insight} onPick={onPickSuggestion} />;
  }

  return (
    <div className="thread" role="log" aria-live="polite" aria-label="Conversation">
      {messages.map((message) => (
        <Message key={message.id} message={message} />
      ))}
      <div ref={endRef} />
    </div>
  );
}
