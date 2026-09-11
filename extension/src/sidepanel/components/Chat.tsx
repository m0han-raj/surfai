import { useEffect, useRef } from 'react';
import type { ChatMessage } from '../../types/agent';
import Message from './Message';

interface ChatProps {
  messages: ChatMessage[];
}

/** The conversation transcript, pinned to the newest message. */
export default function Chat({ messages }: ChatProps) {
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'end', behavior: 'smooth' });
  }, [messages.length]);

  if (messages.length === 0) {
    return (
      <div className="empty">
        <p className="empty__title">Ask about this page</p>
        <p className="empty__body">
          SurfAI can search, filter, navigate and extract information from the page you are
          on. Try &ldquo;find laptops under 80,000&rdquo; or &ldquo;save this as my laptop
          search&rdquo;.
        </p>
      </div>
    );
  }

  return (
    <div className="chat" role="log" aria-live="polite" aria-label="Conversation">
      {messages.map((message) => (
        <Message key={message.id} message={message} />
      ))}
      <div ref={endRef} />
    </div>
  );
}
