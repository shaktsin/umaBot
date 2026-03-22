# CLAUDE.md — Instructions for Claude Code

## PR Creation Workflow

Before creating any PR, run through all steps below in order.
Do NOT skip a step. Do NOT push if any step fails.

---

### Step 1 — Secret / key scan

Scan the full diff for patterns that look like credentials:

```bash
git diff HEAD | grep -iE \
  "(api[_-]?key|secret|token|password|bearer|sk-[a-z0-9]{20,}|AIza[0-9A-Za-z_-]{35}|AKIA[0-9A-Z]{16})" \
  | grep -v "^---" | grep -v "^+++" | grep "^+"
```

If ANY matches are found:
1. Report them to the user.
2. Do NOT commit or push until the user removes them.
3. If the match is a false positive (e.g. a variable name, not a real key), confirm with the user first.

---

### Step 2 — Run tests

```bash
make test
```

- If tests pass (or `No tests found`): proceed.
- If tests fail: fix the failures or report them to the user before creating the PR.

---

### Step 3 — Run linter

```bash
make lint
```

- Report any errors to the user. Minor warnings are acceptable; errors are not.

---

### Step 4 — Commit message format

Use conventional commit style. Lead with the *why*, not just the *what*.

```
<type>(<scope>): <short imperative summary>

<Body — 2-5 lines explaining the motivation and what changed>
<Bullet the key changes if there are several>

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
```

Types: `feat`, `fix`, `refactor`, `chore`, `docs`, `test`

**Good example:**
```
feat(llm): add provider-agnostic retry and token-rate limiting

Replace urllib + asyncio.to_thread with aiohttp so rate-limit
sleeps are truly async. Add TokenBucket (sliding 60-s window) shared
across all LLM clients so total token spend stays under the configured
budget. Retry-delay logic is a hook each provider can override
(Gemini reads from JSON body; Claude/OpenAI use retry-after header).
```

---

### Step 5 — Stage only relevant files

Never `git add -A` blindly. Stage specific files:

```bash
git add <file1> <file2> ...
```

Confirm nothing sensitive is staged:
```bash
git diff --cached --name-only
```

---

### Step 6 — PR body

Use `.github/pull_request_template.md` as the PR body structure. Fill in:
- **Summary**: 2-4 bullets on what and why
- **Changes table**: key files and what changed in each
- **Testing**: what was run and how it passed
- **Security checklist**: confirm all boxes
- **Breaking changes**: explicitly call out any API/config changes

---

### Step 7 — Push and open PR

```bash
git push -u origin <branch>
gh pr create --title "..." --body "$(cat <<'EOF'
...filled template...
EOF
)"
```

Return the PR URL to the user.

---

## Branch naming

`<type>/<short-slug>` — e.g. `feat/llm-rate-limiting`, `fix/claude-tool-name`

## .gitignore reminders

Never commit:
- `*.env`, `*.session`, `config.yaml`, `*.db`, `*.pid`
- `node_modules/`, `package-lock.json` at project root
- `.venv/`, `__pycache__/`
- Any file containing a real API key or secret
