import { useEffect, useState } from 'react';
import { RefreshCw, MessageSquare, Trash2 } from 'lucide-react';
import type { Task } from '@shared/types';
import { api, ApiError, type ConversationSummary } from '../../services/api';
import TaskHistory from '../components/TaskHistory';

interface HistoryPageProps {
  onOpenConversation: (id: string) => void;
}

/**
 * What happened before: conversations, and agent tasks.
 *
 * They are listed apart because they answer different questions. A task is
 * something SurfAI did to a page and is worth reviewing step by step; a
 * conversation is something you had, and is worth reopening and continuing.
 * Merging them into one feed would make both harder to scan.
 */
export default function HistoryPage({ onOpenConversation }: HistoryPageProps) {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      // Together, so one slow list does not stall the other.
      const [conversationsResponse, tasksResponse] = await Promise.all([
        api.listConversations(),
        api.listTasks(),
      ]);
      setConversations(conversationsResponse.conversations);
      setTasks(tasksResponse.tasks);
    } catch (err) {
      setError(
        err instanceof ApiError && err.isNetwork
          ? 'Backend not reachable. History is stored on the server.'
          : err instanceof Error
            ? err.message
            : 'Could not load history.',
      );
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function remove(id: string) {
    // eslint-disable-next-line no-alert
    if (!window.confirm('Delete this conversation?')) return;
    await api.deleteConversation(id);
    setConversations((current) => current.filter((c) => c.id !== id));
  }

  return (
    <section className="section">
      <h2 className="section__header section__header--static">
        <span>Conversations</span>
        <button
          type="button"
          className="button button--icon"
          onClick={load}
          aria-label="Refresh history"
          title="Refresh"
          disabled={loading}
        >
          <RefreshCw size={12} className={loading ? 'spinner' : undefined} aria-hidden="true" />
        </button>
      </h2>

      <div className="section__body">
        {error && (
          <div className="banner banner--danger" role="alert" style={{ margin: 0 }}>
            <span>{error}</span>
          </div>
        )}

        {!error && !loading && conversations.length === 0 && (
          <p className="text-sm text-muted">
            Nothing yet. Conversations are saved as you have them, so you can pick one up later.
          </p>
        )}

        <ul className="convo-list">
          {conversations.map((conversation) => (
            <li key={conversation.id} className="convo">
              <button
                type="button"
                className="convo__open"
                onClick={() => onOpenConversation(conversation.id)}
                title="Open this conversation"
              >
                <MessageSquare size={12} aria-hidden="true" />
                <span className="convo__title">{conversation.title || 'Untitled'}</span>
                <span className="convo__meta">
                  {conversation.message_count} message
                  {conversation.message_count === 1 ? '' : 's'}
                  {conversation.updated_at ? ` · ${relative(conversation.updated_at)}` : ''}
                </span>
              </button>
              <button
                type="button"
                className="button button--icon"
                onClick={() => remove(conversation.id)}
                aria-label={`Delete conversation: ${conversation.title || 'Untitled'}`}
                title="Delete"
              >
                <Trash2 size={12} aria-hidden="true" />
              </button>
            </li>
          ))}
        </ul>
      </div>

      <h2 className="section__header section__header--static">
        <span>Tasks</span>
      </h2>
      <TaskHistory tasks={tasks} loading={loading} error={null} />
    </section>
  );
}

/** "3 minutes ago" beats a timestamp for deciding which thread you wanted. */
function relative(iso: string): string {
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return '';

  const seconds = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (seconds < 60) return 'just now';
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}
