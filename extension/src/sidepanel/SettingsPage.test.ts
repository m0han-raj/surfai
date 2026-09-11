/**
 * Reading a health response defensively.
 *
 * `health?.database.missing_tables` guarded the wrong half: it checked that a
 * response arrived, not that it had the shape we expected. A server on the
 * configured port answered `{"status":"healthy"}` and the panel died with
 * "Cannot read properties of undefined (reading 'missing_tables')".
 *
 * `api.health()` now refuses a response that is not SurfAI's, which is the
 * real fix. This is the second line of defence, for an older or partial
 * backend that identifies itself correctly but does not carry every field.
 */

import { describe, expect, it } from 'vitest';
import { databaseStatusOf } from './pages/SettingsPage';
import type { HealthResponse } from '../services/api';

const healthy = {
  status: 'ok',
  app: 'SurfAI',
  environment: 'development',
  database: { connected: true, error: null, missing_tables: [] },
  auth_provider: 'local',
  agent: { max_steps: 15, max_retries: 2, action_timeout_ms: 10000 },
} as HealthResponse;

describe('databaseStatusOf', () => {
  it('reports a healthy database', () => {
    const status = databaseStatusOf(healthy);
    expect(status.ok).toBe(true);
    expect(status.detail).toBe('Connected');
  });

  it('reports a database that is connected but not migrated', () => {
    const status = databaseStatusOf({
      ...healthy,
      database: { connected: true, error: null, missing_tables: ['tasks', 'favourites'] },
    } as HealthResponse);

    expect(status.ok).toBe(false);
    expect(status.detail).toContain('2');
    expect(status.detail).toMatch(/migration/i);
  });

  it('reports a database that is not connected', () => {
    const status = databaseStatusOf({
      ...healthy,
      database: { connected: false, error: 'OperationalError', missing_tables: [] },
    } as HealthResponse);

    expect(status.ok).toBe(false);
    expect(status.detail).toBe('OperationalError');
  });

  it('survives a response with no health at all', () => {
    expect(() => databaseStatusOf(null)).not.toThrow();
    expect(databaseStatusOf(null).ok).toBe(false);
  });

  it('survives a response carrying no database section', () => {
    // The exact shape that crashed the panel.
    expect(() => databaseStatusOf({ status: 'healthy' } as HealthResponse)).not.toThrow();
    expect(databaseStatusOf({ status: 'healthy' } as HealthResponse).ok).toBe(false);
  });

  it('survives a database section with no missing_tables', () => {
    // A backend from before that field existed.
    const status = databaseStatusOf({
      ...healthy,
      database: { connected: true, error: null },
    } as HealthResponse);

    expect(status.ok).toBe(true);
    expect(status.detail).toBe('Connected');
  });
});
