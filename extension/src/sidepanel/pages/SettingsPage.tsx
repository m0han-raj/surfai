import { useCallback, useEffect, useState } from 'react';
import { Check, X, RotateCcw, Save } from 'lucide-react';
import {
  DEFAULT_SETTINGS,
  getSettings,
  resetSettings,
  saveSettings,
  type SurfAISettings,
} from '../../services/storage';
import { api, type HealthResponse, type LlmHealthResponse } from '../../services/api';
import {
  getAccountEmail,
  isAuthConfigured,
  isSignedIn,
  signIn,
  signOut,
} from '../../services/auth';

/**
 * Settings and diagnostics.
 *
 * The connection panel exists because the most common failure by far is "the
 * backend or the model is not running". Showing both plainly turns an opaque
 * error into a two-second fix.
 *
 * Note what is *not* here: no API key field. Model credentials live in the
 * backend's environment and are never exposed to the extension.
 */
export default function SettingsPage() {
  const [settings, setSettings] = useState<SurfAISettings>(DEFAULT_SETTINGS);
  const [saved, setSaved] = useState(false);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [llm, setLlm] = useState<LlmHealthResponse | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const [account, setAccount] = useState<{ signedIn: boolean; email: string | null }>({
    signedIn: false,
    email: null,
  });
  const [authError, setAuthError] = useState<string | null>(null);
  const [authBusy, setAuthBusy] = useState(false);

  useEffect(() => {
    void getSettings().then(setSettings);
  }, []);

  async function checkConnections() {
    setChecking(true);
    setHealthError(null);
    setHealth(null);
    setLlm(null);
    try {
      setHealth(await api.health());
      setLlm(await api.llmHealth());
    } catch (error) {
      setHealthError(
        error instanceof Error ? error.message : 'Could not reach the backend.',
      );
    } finally {
      setChecking(false);
    }
  }

  useEffect(() => {
    void checkConnections();
    // Re-check whenever the backend address changes.
  }, [settings.backendUrl]);

  const refreshAccount = useCallback(async () => {
    if (!isAuthConfigured()) return;
    setAccount({ signedIn: await isSignedIn(), email: await getAccountEmail() });
  }, []);

  useEffect(() => {
    void refreshAccount();
  }, [refreshAccount]);

  async function handleSignIn() {
    setAuthBusy(true);
    setAuthError(null);
    try {
      await signIn();
      await refreshAccount();
      await checkConnections();
    } catch (error) {
      setAuthError(error instanceof Error ? error.message : 'Sign-in failed.');
    } finally {
      setAuthBusy(false);
    }
  }

  async function handleSignOut() {
    setAuthBusy(true);
    setAuthError(null);
    await signOut();
    await refreshAccount();
    setAuthBusy(false);
  }

  async function handleSave(event: React.FormEvent) {
    event.preventDefault();
    const next = await saveSettings(settings);
    setSettings(next);
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  }

  // Connecting says nothing about whether the migrations have run, and a
  // deployment pointed at an empty database otherwise looks perfectly healthy
  // right up until the first request fails.
  const missingTables = health?.database.missing_tables ?? [];

  return (
    <section className="section">
      <h2 className="section__header section__header--static">
        <span>Settings</span>
      </h2>

      <form className="section__body" onSubmit={handleSave}>
        <div className="field">
          <label className="field__label" htmlFor="backend-url">
            Backend address
          </label>
          <input
            id="backend-url"
            className="input"
            type="url"
            value={settings.backendUrl}
            onChange={(event) =>
              setSettings({ ...settings, backendUrl: event.target.value })
            }
          />
          <p className="field__hint">
            Where the SurfAI API is running. Found automatically on first run;
            change it to point at a hosted backend.
          </p>
        </div>

        <div className="field">
          <label className="field__label" htmlFor="max-elements">
            Elements per page snapshot: {settings.maxElements}
          </label>
          <input
            id="max-elements"
            className="input"
            type="range"
            min={20}
            max={150}
            step={10}
            value={settings.maxElements}
            onChange={(event) =>
              setSettings({ ...settings, maxElements: Number(event.target.value) })
            }
          />
          <p className="field__hint">
            Fewer elements keep the model fast and cheap; more helps on dense pages.
          </p>
        </div>

        <div className="field">
          <label className="field__label" htmlFor="action-timeout">
            Action timeout (ms)
          </label>
          <input
            id="action-timeout"
            className="input"
            type="number"
            min={1000}
            max={60000}
            step={1000}
            value={settings.actionTimeoutMs}
            onChange={(event) =>
              setSettings({ ...settings, actionTimeoutMs: Number(event.target.value) })
            }
          />
        </div>

        <div className="field">
          <label className="field__label" htmlFor="theme">
            Theme
          </label>
          <select
            id="theme"
            className="select"
            value={settings.theme}
            onChange={(event) =>
              setSettings({
                ...settings,
                theme: event.target.value as SurfAISettings['theme'],
              })
            }
          >
            <option value="light">Light</option>
            <option value="dark">Dark</option>
            <option value="system">Match system</option>
          </select>
        </div>

        <div className="checkbox-row">
          <input
            id="auto-low-risk"
            type="checkbox"
            checked={settings.autoRunLowRisk}
            onChange={(event) =>
              setSettings({ ...settings, autoRunLowRisk: event.target.checked })
            }
          />
          <label htmlFor="auto-low-risk" className="text-sm">
            Run low-risk actions automatically
            <span className="field__hint">
              Purchases, payments, deletions and account changes always require confirmation,
              regardless of this setting.
            </span>
          </label>
        </div>

        <div className="row" style={{ justifyContent: 'flex-end' }}>
          <button
            type="button"
            className="button"
            onClick={async () => setSettings(await resetSettings())}
          >
            <RotateCcw size={12} aria-hidden="true" />
            Reset
          </button>
          <button type="submit" className="button button--primary">
            {saved ? <Check size={12} aria-hidden="true" /> : <Save size={12} aria-hidden="true" />}
            {saved ? 'Saved' : 'Save'}
          </button>
        </div>
      </form>

      <h2 className="section__header section__header--static">
        <span>Connection</span>
        <button
          type="button"
          className="button button--ghost button--sm"
          onClick={checkConnections}
          disabled={checking}
        >
          {checking ? 'Checking' : 'Check again'}
        </button>
      </h2>

      <div className="section__body stack">
        {healthError && (
          <div className="banner banner--danger" role="alert" style={{ margin: 0 }}>
            <X size={14} aria-hidden="true" />
            <span>{healthError}</span>
          </div>
        )}

        <StatusRow
          label="Backend"
          ok={Boolean(health)}
          detail={health ? `${health.app} (${health.environment})` : 'Not reachable'}
        />
        <StatusRow
          label="Database"
          ok={Boolean(health?.database.connected) && !missingTables.length}
          detail={
            !health?.database.connected
              ? (health?.database.error ?? 'Not connected')
              : missingTables.length
                ? `Connected, but ${missingTables.length} table(s) are missing. Run the migrations.`
                : 'Connected'
          }
        />
        <StatusRow
          label="Language model"
          ok={Boolean(llm?.reachable)}
          detail={
            llm?.reachable
              ? `${llm.model}${llm.model_available === false ? ' (not installed on the runtime)' : ''}`
              : (llm?.error ?? 'Not reachable')
          }
        />
        {llm && (
          <p className="text-xs text-muted">
            Endpoint: <span className="mono">{llm.base_url}</span>
            {llm.api_key_configured ? ' (API key configured)' : ' (no API key required)'}
          </p>
        )}
      </div>

      {/*
        Only shown when this build has an OAuth client. A local backend runs
        unauthenticated, and offering a sign-in there would be noise.
      */}
      {isAuthConfigured() && (
        <>
          <h2 className="section__header section__header--static">
            <span>Account</span>
          </h2>
          <div className="section__body stack">
            <p className="text-sm text-muted">
              A hosted backend keeps each person's saved pages and history separate, so it
              needs you to sign in. A local backend does not.
            </p>

            {authError && (
              <div className="banner banner--danger" role="alert" style={{ margin: 0 }}>
                <X size={14} aria-hidden="true" />
                <span>{authError}</span>
              </div>
            )}

            <StatusRow
              label="Google"
              ok={account.signedIn}
              detail={
                account.signedIn ? (account.email ?? 'Signed in') : 'Not signed in'
              }
            />

            <div className="row" style={{ justifyContent: 'flex-end' }}>
              {account.signedIn ? (
                <button
                  type="button"
                  className="button"
                  onClick={handleSignOut}
                  disabled={authBusy}
                >
                  Sign out
                </button>
              ) : (
                <button
                  type="button"
                  className="button button--primary"
                  onClick={handleSignIn}
                  disabled={authBusy}
                >
                  {authBusy ? 'Signing in' : 'Sign in with Google'}
                </button>
              )}
            </div>
          </div>
        </>
      )}

      <h2 className="section__header section__header--static">
        <span>Privacy</span>
      </h2>
      <div className="section__body">
        <p className="text-sm text-muted">
          SurfAI sends a compact summary of the page you are on -- labelled controls and a short
          text excerpt -- to your configured model. Full page HTML is never stored, and password
          and payment fields are stripped before anything leaves the page.
        </p>
      </div>
    </section>
  );
}

function StatusRow({ label, ok, detail }: { label: string; ok: boolean; detail: string }) {
  return (
    <div className="row" style={{ justifyContent: 'space-between' }}>
      <span className="text-sm">{label}</span>
      <span className={`status ${ok ? 'status--completed' : 'status--failed'}`}>
        {ok ? <Check size={11} aria-hidden="true" /> : <X size={11} aria-hidden="true" />}
        {detail}
      </span>
    </div>
  );
}
