# Contributing to SurfAI

Thanks for your interest. This document covers how to get set up, what the project expects from a
change, and the few rules that are not negotiable.

---

## Getting set up

```bash
git clone https://github.com/m0han-raj/surfai.git
cd surfai
cp .env.example .env
```

**Backend**

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate        # Windows
# source .venv/bin/activate   # macOS / Linux
pip install -r requirements-dev.txt
pytest -q
```

**Extension**

```bash
cd extension
npm install
npm test
npm run build
```

Neither suite requires PostgreSQL or a language model. The backend tests run on SQLite with a
scripted fake provider; the extension tests run in jsdom.

**Full stack**

```bash
docker compose up -d --build
cd demo && python -m http.server 5500
```

No GPU or downloaded model? Use the scripted stub:

```bash
python backend/tools/mock_llm_server.py --port 11500 --verbose
```

and point `LLM_BASE_URL` at `http://host.docker.internal:11500/v1`. It drives the real loop
deterministically, which also makes it the fastest way to reproduce an agent-loop bug.

---

## Before you open a pull request

```bash
cd backend && pytest -q && ruff check app tests migrations tools
cd extension && npm test && npm run typecheck && npm run build
```

All four must pass. Do not leave a known build or test failure for a reviewer to find.

---

## Rules that are not negotiable

These protect the property that makes SurfAI safe: **the model is never the security boundary.**

### 1. Never let model output reach the browser unvalidated

Every action goes through `validate_action`. If you add a code path that executes something the
model produced, it must pass through that function first.

### 2. Never add an action that can execute code

The vocabulary is seven verbs. Adding `EVAL`, `EXECUTE_SCRIPT`, `SET_HTML`, or an action that
accepts a CSS selector or raw JavaScript will be rejected. If you believe a task genuinely needs
one, open an issue and describe the task. There is almost always a way to express it with the
existing verbs.

### 3. Never let model output influence risk classification

`app/browser/risk.py` is pure Python over the action and its target element. Do not add a parameter
that lets the planner declare its own risk level, skip confirmation, or mark an action pre-approved.
A page can say anything; the classifier must not listen.

### 4. Never weaken the confirmation gate to make a flow smoother

If a task is annoying because it asks too often, fix the *classifier's precision* with a test.
Do not widen the auto-execute path.

### 5. Never capture credentials

Password, card, CVV and OTP values are stripped in the page. Do not add a code path that reads
them, logs them, or sends them anywhere.

### 6. Never commit secrets

`.env` is gitignored. `.env.example` holds placeholders only. No API keys in source, tests,
fixtures, or commit messages.

A change touching `security/`, `browser/risk.py`, `browser/validation.py`, or the content script's
executor should come with tests demonstrating the property it preserves.

---

## Code style

**Python.** Ruff (`line-length = 100`, py312 target). Type hints on public functions. Pydantic for
anything crossing a boundary. `ruff check app tests migrations` must be clean.

**TypeScript.** `strict: true`, `noUnusedLocals`, `noUnusedParameters`. `npm run typecheck` must be
clean. Prefer explicit types on exported functions.

**Comments.** Explain *why*, not *what*. A comment that restates the code is noise; one that
records a non-obvious constraint is valuable:

```python
# `[ _-]?` rather than `[_-]?`: real labels read "Card number" and "API Key".
```

**Naming.** Match the surrounding code. `snake_case` in Python, `camelCase` in TypeScript,
`SCREAMING_SNAKE` for constants in both.

---

## UI rules

The interface is a professional developer tool, and the constraints are firm:

- **No emojis.** Anywhere. Not in buttons, headings, status text, errors, placeholders or
  notifications. Use Lucide icons, SVG, or a text label.
- **No decoration.** No gradients, neon, glassmorphism, or animation beyond a loading spinner.
- **Accessible by default.** Semantic HTML, keyboard navigation, visible focus, ARIA labels on
  icon-only controls, sufficient contrast. Never convey state by colour alone; pair it with an icon
  or text.
- **Tokens, not hard-coded colours.** Everything comes from `src/styles/theme.css`, and every token
  is defined for both light and dark.
- **Never expose chain-of-thought.** The activity log shows what SurfAI *did*
  ("Reading page", "Applying filter"), not how it reasoned.

---

## Testing expectations

A behaviour change needs a test. A bug fix needs a test that fails before it and passes after.

Test the **property**, not the implementation. `test_injected_page_text_cannot_lower_risk` stays
meaningful when the classifier is rewritten; a test asserting a specific regex does not.

The demo pages in `demo/` are part of the test surface: `test_demo_pages.py` and
`demo-pages.test.ts` load them from disk. If you change a demo page, run both suites.

When adding an agent-loop test, script the fake LLM with `fake_llm.push(...)`. The fake validates
every scripted response against the caller's real schema, so a fixture that would break the real
contract fails in the test too.

---

## Keeping the shared contract in sync

`shared/action-schema.ts` and `backend/app/browser/action_schema.py` describe the same seven
actions. They are kept honest by
`test_action_validation.py::test_action_vocabulary_matches_shared_schema`, which parses the
TypeScript file. Change one, change the other, and let that test confirm it.

---

## Database changes

Model changes need an Alembic migration:

```bash
cd backend
alembic revision --autogenerate -m "describe the change"
alembic upgrade head
alembic downgrade -1   # verify it reverses cleanly
alembic upgrade head
```

Use `JSONType` (JSONB on PostgreSQL, JSON elsewhere) so the same migration runs against the SQLite
fallback used in development and tests.

---

## Commits and pull requests

Write commit subjects in the imperative mood, explaining the change and, when it is not
obvious, why:

```
Reject off-site NAVIGATE without confirmation

Redirecting the agent to another origin is a common injection payload, so
it is treated as MEDIUM risk even though navigation is otherwise low risk.
```

A pull request should say what changed, why, how you verified it, and anything a reviewer should
look at closely. If it touches the security layer, say so explicitly.

---

## Good first contributions

- Broaden `element-detector.ts` to recognise more custom widget patterns
- Add heuristics to `tool_discovery.py` for capability shapes not yet covered
- Improve `extractRepeatedItems` for layouts that currently fall back to page text
- Add injection payload shapes to `prompt_injection.py` (**with tests, and check the benign
  corpus still passes**)
- Dark-mode polish; the tokens exist and are complete
- Add a demo site exercising a pattern the current three do not

---

## Questions

Open an issue. For anything security-sensitive, use a private advisory instead; see
[SECURITY.md](SECURITY.md).
