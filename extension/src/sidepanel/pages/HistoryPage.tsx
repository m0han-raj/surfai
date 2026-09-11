import { useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import type { Task } from '@shared/types';
import { api, ApiError } from '../../services/api';
import TaskHistory from '../components/TaskHistory';

/** Task history (AC-13): completed, failed and cancelled runs. */
export default function HistoryPage() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const response = await api.listTasks();
      setTasks(response.tasks);
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

  return (
    <section className="section">
      <h2 className="section__header section__header--static">
        <span>History</span>
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

      <TaskHistory tasks={tasks} loading={loading} error={error} />
    </section>
  );
}
