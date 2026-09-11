# Deploying the SurfAI backend

This covers a hosted backend that several people share. If you only ever use
SurfAI yourself, do not deploy it: run `docker compose up` locally and skip this
entire document. A local backend needs no auth, costs nothing, and keeps page
content on your machine.

---

## Before you start

Three things must be true, and the first is not optional.

**1. `AUTH_PROVIDER` must be `google`.** The `local` provider grants a fixed
identity to every request. It has no login to bypass because it has no login.
Deploy it publicly and anyone who finds the URL reads and writes everyone's
favourites and task history. The backend logs a warning on startup if you get
this wrong, but nothing stops you.

**2. You need a Google OAuth client id.** The extension authenticates with it
and the backend checks every token was issued for it. Without that check, a
token minted for any other Google application would be accepted here.

**3. You need somewhere to run a model.** A hosted backend cannot reach a model
on your laptop. Either run one alongside the backend, or point `LLM_BASE_URL` at
a hosted OpenAI-compatible provider and set `LLM_API_KEY`.

---

## 1. Create the Google OAuth client

1. Open the [Google Cloud console](https://console.cloud.google.com/apis/credentials)
   and create a project.
2. Configure the OAuth consent screen. **External**, and while it is in Testing
   only accounts you list can sign in, which is a reasonable way to start.
3. Create credentials, choosing **Chrome Extension** as the application type.
4. Enter your extension's ID.

Getting a stable extension ID before publishing: build the extension, load it
unpacked, and copy the ID from `chrome://extensions`. That ID is derived from the
directory path and changes if you move the folder. To pin it, add the `key` from
a packed `.crx` to `manifest.json`. Publishing to the Web Store assigns a
permanent ID, so most people set this up after a first upload.

Then add the client id to the extension manifest:

```json
{
  "oauth2": {
    "client_id": "YOUR_ID.apps.googleusercontent.com",
    "scopes": ["openid", "email"]
  }
}
```

Only ask for `openid` and `email`. The backend derives identity from the token's
subject and uses email for display and the optional allowlist. Requesting more
means a scarier consent screen for no benefit.

---

## 2. Deploy

Any host that runs a container works. `fly.toml` here is a worked example.

```bash
fly launch --no-deploy --copy-config --config deploy/fly.toml
fly postgres create --name surfai-db
fly postgres attach surfai-db            # sets DATABASE_URL for you

fly secrets set \
  AUTH_PROVIDER=google \
  GOOGLE_CLIENT_ID=YOUR_ID.apps.googleusercontent.com \
  LLM_BASE_URL=https://your-model-host/v1 \
  LLM_API_KEY=your-key \
  LLM_MODEL=gpt-oss-20b

fly deploy --config deploy/fly.toml
```

Migrations run from the container entrypoint on every boot, so there is no
separate release step.

Verify:

```bash
curl https://your-app.fly.dev/health
curl https://your-app.fly.dev/health/llm
curl -i https://your-app.fly.dev/api/favourites    # expect 401
```

That last one matters. **A 200 means you deployed with `AUTH_PROVIDER=local` and
the instance is open to anyone.** Fix it before going further.

---

## 3. Point the extension at it

In the SurfAI side panel, Settings, set the backend address to your deployed
URL. Sign in with Google when prompted.

The extension attaches the token to every request and refreshes it once on a
401, because Chrome caches tokens and will otherwise keep handing back one the
server has already rejected.

---

## Restricting who can sign in

By default any Google account that authenticates with your client id is
accepted. To run it privately:

```bash
fly secrets set ALLOWED_EMAILS="you@example.com,colleague@example.com"
```

Requests from anyone else are rejected after their token verifies, so it is a
genuine check rather than a UI nicety.

---

## What is and is not handled

**Handled.** Token verification against Google on every cache miss, with the
audience checked against your client id. Identity derived from the token's
stable subject rather than the email, which can be reassigned. Per-user
isolation on favourites, tasks and agent sessions, covered by tests. Agent state
in PostgreSQL, so a request can be served by a different instance than the one
that started the task, and a deploy does not strand tasks in flight. Stale
sessions purged at startup. Failing closed when Google cannot be reached.

**Not handled.** There is no rate limiting: a signed-in user can run as many
tasks as they like, and each one costs model tokens. There is no billing, quota
or per-user cost cap. There is no audit log beyond application logs. Token
verification results are cached in each instance's memory for up to five
minutes, so revoking access can take that long to take effect. And none of this
changes the prompt-injection position: the deterministic layers bound what a
hostile page can achieve, but SurfAI acts with whatever access the browser
already has.

Read [SECURITY.md](../SECURITY.md) before exposing this to anyone but yourself.

---

## Cost

The backend is IO-bound and small; the model dominates. A shared-cpu-1x
instance with 512MB is enough, and with `auto_stop_machines` it suspends when
idle. The database is the only thing that must stay up.

If you point `LLM_BASE_URL` at a paid provider, every agent step is a request.
`MAX_AGENT_STEPS` (15 by default) is the per-task ceiling and the main thing to
turn down if cost matters more than capability.
