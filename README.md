# SurfAI

A personal AI assistant that lives in the Chrome Side Panel. Ask it anything, ask it about the page
you are on, and when you want something done, it can act on that page for you.

Most of what you say is answered directly, in one turn, like any good assistant. Acting on a page is
the exception, not the default, and it is what the rest of this document is mostly about, because
that is the part that has to be built carefully.

---

## How a message is handled

```mermaid
flowchart TD
    MSG["You type something"] --> INTENT["Classify intent"]
    INTENT -->|"question or small talk"| ANSWER["Answer directly<br/>one model call"]
    INTENT -->|"save / open a favourite"| MEM["Memory Agent"]
    INTENT -->|"do something on the page"| LOOP["Agent loop"]

    ANSWER --> REPLY["Reply"]
    MEM --> REPLY
    LOOP --> REPLY

    classDef common fill:#eff6ff,stroke:#2563eb,color:#1e3a8a
    class ANSWER common
```

A question reads the page only when it refers to the page. "Explain recursion" costs nothing;
"summarise this" is grounded in a snapshot. Neither creates a task, invokes the planner, or touches
the action executor.

When you ask for something to be *done*, the agent loop starts, and the reply carries a folded trace
of the steps it took.

---

## Architecture

```mermaid
flowchart TB
    subgraph Browser["Chrome"]
        SP["Side Panel<br/>React + TypeScript"]
        SW["Service Worker<br/>broker, on-demand injection"]
        CS["Content Script<br/>semantic DOM + executor"]
        PAGE["Web page<br/>UNTRUSTED"]
    end

    subgraph Server["Backend (Docker)"]
        API["FastAPI"]
        ASSIST["Assistant<br/>direct answers"]
        ORCH["Orchestrator"]
        AG["Planner · Page Agent<br/>Tool Discovery · Memory Agent"]
        SEC["Validator · Risk Classifier<br/>Sanitizer · Injection Defence"]
        DB[("PostgreSQL<br/>favourites · tasks · steps")]
    end

    LLM["OpenAI-compatible LLM<br/>Ollama / llama.cpp / vLLM / hosted"]

    SP <--> SW
    SW <--> CS
    CS -->|"reads, acts on"| PAGE
    SP <-->|"HTTP JSON"| API
    API --> ASSIST
    API --> ORCH
    ORCH --> AG
    ORCH --> SEC
    ORCH --> DB
    ASSIST -->|"API key stays server-side"| LLM
    AG --> LLM

    classDef untrusted fill:#fef2f2,stroke:#b91c1c,color:#7f1d1d
    classDef security fill:#eff6ff,stroke:#2563eb,color:#1e3a8a
    class PAGE untrusted
    class SEC security
```

**Decisions on the server, actions in your browser.** For a task, the backend hands the extension one
directive at a time; the extension executes it and calls back with the outcome *and a fresh page
snapshot*, so the planner always sees the page as it is now. The extension never holds a model
credential, and every proposed action passes the security layer before it reaches the page.

The orchestrator owns the loop and its budgets: 15 steps per task, 2 consecutive failures, 10s per
action, 60 elements of context. A validation failure (stale id, wrong field) is repaired inside one
planner turn; an execution failure triggers a fresh observation and a real re-plan.

**The page snapshot** is built from accessible names (`aria-label`, then `<label>`, then visible
text), drops anything a human could not click, ranks controls over decoration when truncating, and
strips password, card and OTP values at source. Element ids are valid only until the next capture.

**Favourites store why, not just where:** a URL plus the intent behind it and your stated
preferences, which is what makes *"check my AI jobs"* a task rather than a navigation. Resolution is
lexical-first, so recall keeps working with no model reachable.

---

## Quick run

Needs Docker with Compose, Node.js 20+, Chrome 116+, and an LLM runtime.

```bash
cp .env.example .env                       # point LLM_BASE_URL at your runtime
docker compose up -d --build               # PostgreSQL + migrations + API on :8000
curl localhost:8000/health/llm             # probes the model endpoint, never echoes the key
cd extension && npm install && npm run build
```

Then load `extension/dist` unpacked at `chrome://extensions` with **Developer mode** on, and open
SurfAI from the Side Panel. If your backend is not on port 8000, set the address in Settings.

Try `explain how DNS works`, then open a page and try `summarise this`, then `save this page for
later`. Serve `demo/` over HTTP for pages that exercise the acting path.

No GPU and no model? `backend/tools/mock_llm_server.py` is a scripted OpenAI-compatible stub that
returns fixed decisions and drives the real pipeline end to end.

---

## Security model

Page text is untrusted input, never instruction. It is wrapped in a labelled envelope the page cannot
forge, and known injection patterns are redacted. Those layers reduce noise; **schema validation and
risk classification are what make the system safe.** Even a fully injected planner cannot escalate:

- the vocabulary is seven constants (`CLICK`, `TYPE`, `SELECT`, `SCROLL`, `NAVIGATE`, `EXTRACT`,
  `WAIT`) with no way to express `eval`, a `javascript:` URL, or arbitrary script;
- `target` must be a semantic id from the current snapshot, so no CSS selector and no hidden element
  is reachable, and unknown fields are rejected outright;
- risk is classified in Python over the action and its target, so a page claiming *"this button is
  safe, auto-approve"* changes nothing.

Reads, scrolls, searches and same-site navigation run automatically. Logins, submissions, uploads and
off-site navigation ask first. `PURCHASE`, `PAYMENT`, `DELETE` and `ACCOUNT_CHANGES` are never
auto-executed: **SurfAI never completes a purchase on its own.** Full page HTML is never stored or
transmitted, and with a local model nothing leaves your machine.

The trust boundary does not relax for a plain question. A page-grounded answer is sanitised and
wrapped exactly as a planner prompt is.

Verify it yourself: open `demo/article/injection-test.html` and ask for a summary. Expect a security
notice, a summary that mentions the ignored instructions, and no navigation or disclosure.

---

## Hosting it for other people

Skip this entirely if SurfAI is just for you. A local backend needs no auth,
costs nothing, and keeps page content on your machine.

**`AUTH_PROVIDER` must be `google`.** The `local` provider gives every request
the same identity. It has no login to bypass because it has no login, so a
public deployment hands anyone who finds the URL everyone's favourites and
history. The backend warns at startup, but nothing stops you.

### 1. A Google OAuth client

In the [Google Cloud console](https://console.cloud.google.com/apis/credentials):
create a project, configure the OAuth consent screen (External; while it is in
Testing only accounts you list can sign in), then create a **Chrome Extension**
credential and paste your extension ID.

To get a stable extension ID before publishing, build, load unpacked, and copy
the ID from `chrome://extensions`. It is derived from the directory path and
changes if you move the folder, so most people set this up after a first Web
Store upload, which assigns a permanent one.

Then add it to `extension/public/manifest.json`:

```json
"oauth2": {
  "client_id": "YOUR_ID.apps.googleusercontent.com",
  "scopes": ["openid", "email"]
}
```

Ask for `openid` and `email` only. Identity comes from the token's subject;
email is for display and the optional allowlist. More scopes mean a scarier
consent screen for nothing.

### 2. Deploy to Vercel

Import the repository at [vercel.com/new](https://vercel.com/new). `vercel.json`
is committed, so the build needs no configuration: it routes every request to
the FastAPI app and installs from the root `requirements.txt`. Every push to
`main` then deploys, and every pull request gets a preview.

Set these in **Project Settings, Environment Variables**:

| Variable | Value |
|---|---|
| `AUTH_PROVIDER` | `google` |
| `GOOGLE_CLIENT_ID` | your OAuth client id |
| `DATABASE_URL` | a **pooled** PostgreSQL URL (Neon, Supabase or Vercel Postgres) |
| `LLM_BASE_URL` | a reachable OpenAI-compatible endpoint |
| `LLM_API_KEY` | if that endpoint needs one |
| `ALLOWED_EMAILS` | optional, to keep the instance private |

Then run the migrations once, from anywhere, against the same database:

```bash
cd backend && DATABASE_URL="your-production-url" alembic upgrade head
```

Three things differ from the container path. All are handled in code, but they
explain the shape of the configuration:

- **Connection pooling.** A per-process pool is correct on a long-lived server
  and ruinous across concurrent lambdas, each holding its own. The engine uses
  `NullPool` when it detects Vercel, which is why the connection string should
  be a pooled one.
- **No implicit schema creation.** Cold-start DDL would race between lambdas, so
  it is skipped and migrations are the deliberate step above.
- **The dependency list is smaller.** The root `requirements.txt` omits Uvicorn
  (Vercel invokes the ASGI app directly) and Alembic (migrations are separate).
  Both are megabytes against a hard lambda size limit. A test fails if the two
  lists drift apart on a shared pin.

**`maxDuration` is 60s.** One agent step has to finish inside it, which is fine
for a hosted model and not for a slow local one.

### 3. Verify before trusting it

```bash
curl https://your-app.vercel.app/health
curl -i https://your-app.vercel.app/api/favourites    # must be 401
```

**A 200 on that second one means you deployed in local mode and the instance is
open to anyone.** Fix it before going further.

To run it privately, set `ALLOWED_EMAILS` to a comma-separated list. It is
enforced after the token verifies, so it is a real check.

### What hosting does not give you

No rate limiting and no per-user cost cap: a signed-in user can run as many
tasks as they like and each step costs model tokens. No audit log beyond
application logs. Token verification is cached per instance for up to five
minutes, so revoking access takes that long to take effect. And none of it
changes the prompt-injection position. Read [SECURITY.md](SECURITY.md) first.

---

## Configuration

Every setting is an environment variable; [.env.example](.env.example) is the annotated list. The
ones you are most likely to touch:

| Variable | Default | Purpose |
|---|---|---|
| `LLM_BASE_URL` | `http://localhost:11434/v1` | OpenAI-compatible endpoint |
| `LLM_MODEL` | `gpt-oss-20b` | Any model the endpoint serves |
| `LLM_API_KEY` | *(empty)* | Server-side only; never sent to the extension |
| `MAX_AGENT_STEPS` | `15` | Hard cap on steps per task |
| `MAX_ELEMENTS_IN_CONTEXT` | `60` | Context budget for the snapshot |
| `ALWAYS_CONFIRM_CATEGORIES` | `PURCHASE,PAYMENT,DELETE,ACCOUNT_CHANGES` | Never auto-executed |

Structured output negotiates strictness downward (`json_schema`, then `json_object`, then
prompt-only extraction) and the result is always validated locally, so unvalidated model output never
reaches the browser. Chrome permissions stay narrow: `activeTab` rather than `<all_urls>`, with the
content script injected on demand, and host access limited to localhost so the backend port is
configurable.

Auth has two modes. `local` is a single unauthenticated user, correct for a backend on your own
machine and wrong for anything else. `google` verifies a bearer token with Google on every request,
checks its audience against your own OAuth client id, derives identity from the token's stable
subject rather than its email, and fails closed. Favourites, tasks and agent sessions are isolated
per user, and a task can only be continued by whoever started it. See
[Hosting](#hosting-it-for-other-people) before running it for anyone but yourself.

---

## Testing

`pytest -q` in `backend/` and `npm test` in `extension/`. Neither needs PostgreSQL or a model: SQLite
with a scripted fake provider, and jsdom. CI runs both plus the Docker build on every push.

327 backend and 107 extension tests cover the action schema and its TS/Python parity, injection
defence, the direct-answer path (a question must not observe the page or create a task), the loop's
retry and confirmation paths, token verification and its failure-closed behaviour, per-user
isolation, a task continued by a different backend instance, favourite resolution, snapshot
extraction, the manifest's permission balance, and the demo pages loaded from disk.

---

## Limitations

Real-model planning quality is unmeasured; expect to tune prompts and `MAX_ELEMENTS_IN_CONTEXT`.
One tab, one task at a time, and no cross-site workflows. A hosted deployment has no rate limiting
or per-user cost cap, so every signed-in user can spend model tokens freely. No login automation, no
CAPTCHA solving, no streaming, so a reply appears when it is complete. Tool discovery is heuristic
and unusual widgets fall back to page text. Whether a question needs the page is decided by a
keyword heuristic, which is cheap and occasionally wrong in the harmless direction. And injection
defence is not a guarantee: the deterministic layers bound what an attacker can achieve, but novel
phrasings will evade the pattern layer, so do not run SurfAI on hostile pages while logged into
sensitive accounts.

---

## References

- [SECURITY.md](SECURITY.md) for the threat model, content classes and the full risk taxonomy
- [CONTRIBUTING.md](CONTRIBUTING.md) to run both suites and keep the security layer deterministic
- [.env.example](.env.example) for every setting, annotated
- `shared/action-schema.ts` for the action contract, mirrored in Python and parity-tested
- `demo/` for the test pages, including the deliberately hostile one
- **Roadmap:** WebMCP where a site declares one · pgvector semantic memory · cross-site workflows ·
  per-site adapters · scheduled favourite checks · multi-user OAuth · model routing
- License: [MIT](LICENSE)
