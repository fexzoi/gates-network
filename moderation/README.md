# GTS - GATES NETWORK AI Moderation (staging)

This service is the isolated backend component for GTS marketplace moderation. It includes a same-origin admin dashboard, authenticated Wix review queue, human approve/reject actions, and a CMS publishing adapter. Keep Wix automation disabled until live CMS access and the target collections are verified.

## What it does

- Authenticated `POST /v1/moderate` endpoint.
- Calls the OpenAI Responses API using a strict JSON Schema output.
- Applies a conservative approval threshold: an approval with risk score >= 35 or confidence < 75 is changed to rejection.
- Logs every completed decision and provider failure with timestamp, model, decision, score, reasons and policy flags.
- Authenticated `GET /v1/audit` endpoint.
- Same-origin admin console at `/dashboard` with a live Wix submission queue, review reason, approve-and-publish and reject actions, and audit inspection. The bearer token is held in page memory only and is not saved in browser storage.
- Human approval publishes to `gtsMarketplaceListings` with status `active` (the live marketplace's public visibility status); rejection never creates a public listing.
- Conditional submission state transitions and deterministic public item IDs prevent duplicate listings on retry. All admin decisions are audit-logged.
- SQLite database defaults to `/data/moderation_audit.sqlite3` when the Railway `/data` volume is mounted; `MODERATION_DB_PATH` can override this.
- Repeated completed submissions are idempotent by `submission_id`.
- Provider/configuration failures fail closed; no approval is returned and publication remains disabled.

## Explicit safety boundary

`published` is always `false` for AI moderation results: AI cannot publish. The separate authenticated human admin endpoint can approve-and-publish or reject a Wix submission after an explicit admin action. The publishing workflow requires `WIX_AUTOMATION_ENABLED=true`, valid server-side Wix credentials, and CMS write permissions. Do not enable it until live read-only queue access is verified.

## Run locally

Python 3.11+ recommended.

```bash
cd moderation
python -m venv .venv
# Windows PowerShell: .venv\\Scripts\\Activate.ps1
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env  # Windows; enter secrets in your shell/host, not source control
uvicorn app:app --reload
```

Set these environment variables in your shell or hosting provider's secret settings:

- `OPENAI_API_KEY`: OpenAI API key (never commit it).
- `MODERATION_API_TOKEN`: long random bearer token required by moderation/audit endpoints.
- `OPENAI_MODEL`: model identifier supported by your OpenAI project; defaults to `gpt-4.1-mini`.
- `MODERATION_DB_PATH`: SQLite path. On Railway, set this to `/data/moderation_audit.sqlite3` to keep audit records on the attached `/data` volume. Without an override, the app uses that path when `/data` exists and a local SQLite file otherwise. Confirm the volume is attached before relying on audit retention.

## API

- `GET /health` — public liveness check; reports that publication is disabled.
- `POST /v1/moderate` — requires `Authorization: Bearer <MODERATION_API_TOKEN>` and JSON body with `submission_id`, `title`, `platform`, `category`, `description`, `price_text`, `seller_name`, `seller_contact_url`.
- `GET /v1/audit?limit=50` — same authorization; optional `submission_id` filter.
- `GET /dashboard` — same-origin browser console; enter the bearer token to load audit history and submit manual moderation requests.
- `GET /v1/wix/submissions` — authenticated queue for pending and AI-reviewed submissions.
- `POST /v1/wix/submissions/{submission_id}/decision` — authenticated human decision; `{"action":"approve","reason":"..."}` publishes, while `{"action":"reject","reason":"..."}` rejects without publishing.
- `POST /v1/wix/process-pending?limit=5` — optional AI-only status recommendations; it never publishes and its AI-reviewed items remain available for human review.

## Tests

```bash
pytest -q
```

This service is still staging-only. The dashboard is an operator console, not a public seller form. Do not connect the public seller form or enable Wix automation until the adapter has been validated against real test submissions and field mappings.


## Wix staging adapter (disabled by default)

The authenticated `POST /v1/wix/process-pending` endpoint can query pending submissions and update only the private submission `status` field to `AI Approved - Awaiting Publish` or `AI Rejected`. It **never creates or activates a public listing**. It is disabled unless `WIX_AUTOMATION_ENABLED=true`, and requires server-side `WIX_API_KEY`, `WIX_SITE_ID`, and the verified submissions collection ID.

Before enabling this adapter, verify the Wix API key has only the needed CMS permissions, confirm the exact Wix site ID and collection ID, verify the REST API request shape against the site's API access, and test against non-production submissions. The current adapter is an integration scaffold; do not enable it until these checks are complete. The audit database must be moved to persistent/managed storage before production.
