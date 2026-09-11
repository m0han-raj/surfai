import { ShieldAlert, User, Sparkles, AlertCircle } from 'lucide-react';
import type { ChatMessage } from '../../types/agent';

interface MessageProps {
  message: ChatMessage;
}

/**
 * One turn in the conversation.
 *
 * Rendered as an indented block rather than a chat bubble: in a 360px panel,
 * bubbles waste horizontal space that extracted results need.
 */
export default function Message({ message }: MessageProps) {
  const isUser = message.role === 'user';
  const classes = ['message', isUser ? 'message--user' : 'message--assistant'];
  if (message.error) classes.push('message--error');

  return (
    <article className={classes.join(' ')} aria-label={isUser ? 'Your message' : 'SurfAI response'}>
      <div className="message__role">
        {isUser ? (
          <User size={12} aria-hidden="true" />
        ) : message.error ? (
          <AlertCircle size={12} aria-hidden="true" />
        ) : (
          <Sparkles size={12} aria-hidden="true" />
        )}
        {isUser ? 'You' : 'SurfAI'}
      </div>

      <div className="message__body">{message.content}</div>

      {message.warnings?.map((warning) => (
        <div key={warning} className="message__warning" role="note">
          <ShieldAlert size={14} aria-hidden="true" />
          <span>
            <strong>Security notice.</strong> {warning}
          </span>
        </div>
      ))}
    </article>
  );
}
