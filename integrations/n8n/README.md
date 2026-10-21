# DevFlow + n8n

n8n sits around DevFlow as an integration layer. DevFlow stays in charge of planning, execution, testing and safety; n8n handles notifications, approvals from chat, and triggers from other tools.

```
DevFlow ── signed webhook events ──► n8n ──► Telegram / Slack / email / Sheets / GitHub
   ▲                                  │
   └──── REST API (API key) ◄─────────┘   create project · approve plan · resume · cancel
```

| Workflow file | What it does |
|---|---|
| `devflow-notifications.json` | Receives DevFlow events and sends a Telegram message. `plan_ready` messages include a signed, expiring approval link |
| `devflow-approve-link.json` | Verifies that link and approves the plan through the DevFlow API (only if the plan version still matches) |
| `devflow-new-project-form.json` | An n8n form that creates a DevFlow project and starts planning |

## 1. Start n8n

With Docker Desktop running, from the repository root:

```bash
docker compose up -d n8n
```

Open http://localhost:5678 and create the n8n owner account. Without Docker, `npx n8n` also works; then set the environment variable `NODE_FUNCTION_ALLOW_BUILTIN=crypto` first, and use `http://localhost:8000` instead of `http://host.docker.internal:8000` in the workflows.

n8n in Docker reaches DevFlow at `http://host.docker.internal:8000`. Uvicorn listens only on `127.0.0.1` by default, so start the backend with `--host 0.0.0.0` when n8n runs in Docker:

```bash
.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

This makes the API reachable from your local network too; keep Windows Firewall on, or run n8n with `npx` instead.

## 2. Create credentials

**In DevFlow → Integrations → API keys:** create a key named `n8n` and copy it (shown once).

**In n8n → Credentials → Add credential → Header Auth**, create two:

| Credential name | Header name | Value | Used by |
|---|---|---|---|
| `DevFlow API` | `Authorization` | `Bearer dfk_…` (your API key) | HTTP Request nodes that call DevFlow |
| `DevFlow webhook token` | `X-Webhook-Token` | a random string you choose | The "DevFlow event" Webhook node |

**For Telegram:** create a bot with [@BotFather](https://t.me/BotFather), add a **Telegram API** credential with its token, and find your chat id (message the bot, then open `https://api.telegram.org/bot<token>/getUpdates`). Any other n8n node (Slack, Gmail, Discord) can replace the Telegram node.

## 3. Import the workflows

In n8n: **Workflows → ⋯ → Import from File**, once per JSON file. Then:

1. Open each node with a credential warning and select the matching credential.
2. In **Format message** and **Verify link**, replace `CHANGE-ME-link-secret` with the same long random string in both.
3. In **Send Telegram message**, set your chat id.
4. **Activate** the workflows (toggle at the top right).

## 4. Point DevFlow at n8n

In **DevFlow → Integrations → Webhooks** add:

- URL: `http://localhost:5678/webhook/devflow-events`
- Events: the defaults, or **All events**
- Auth header: `X-Webhook-Token` with the value from the `DevFlow webhook token` credential

Click **Send test**: you should get a Telegram message and a `200` in the delivery list. While a workflow is open in the editor with "Listen for test event", n8n uses `/webhook-test/` instead of `/webhook/`.

## What DevFlow sends

`POST` with JSON:

```json
{
  "id": "evt_412",
  "event": "plan_ready",
  "message": "Plan v1 with 3 tasks awaiting approval",
  "agent": "planner",
  "created_at": "2026-10-22T06:31:02+00:00",
  "project": { "id": 7, "name": "Attendance API", "status": "awaiting_approval", "status_reason": "Plan ready for review", "mode": "multi_agent", "plan_version": 1 },
  "data": { "plan_version": 1, "tasks": ["T1", "T2", "T3"] },
  "links": { "dashboard": "http://localhost:5173/projects/7", "api": "/api/projects/7" }
}
```

Headers: `X-DevFlow-Event`, `X-DevFlow-Delivery`, `X-DevFlow-Timestamp`, and `X-DevFlow-Signature: sha256=<HMAC-SHA256(signing secret, "<timestamp>.<raw body>")>`. The signing secret is shown once when you create the webhook. n8n's parsed body cannot reproduce the exact raw bytes, so these workflows authenticate with the header token instead; services that keep the raw body should verify the signature.

Failed deliveries are retried up to 3 times (0 s, 2 s, 8 s); 4xx responses other than 408/429 are not retried. Each attempt is visible under **Deliveries**. Events that happen while the backend is stopped are not replayed.

## Approving from your phone

Approval links point at `http://localhost:5678`, which only works on the machine running n8n. To approve from a phone, expose n8n through a tunnel (for example `cloudflared tunnel --url http://localhost:5678`), set `WEBHOOK_URL` for n8n to the tunnel URL, and put the same URL in `CONFIG.n8nUrl` in **Format message**. Anyone with a valid link can approve that one plan version until it expires, so keep links private.

## Safety notes

- n8n only triggers and notifies. Whether tests passed, what code is applied, and every safety check stay in DevFlow.
- The API key has the same access as your account; revoke it in DevFlow if it leaks.
- Approval links are HMAC-signed, expire after 24 hours, and carry the plan version, so a link for a superseded plan is refused (HTTP 409).
- Payloads contain ids, statuses and messages, never source code or secrets.
