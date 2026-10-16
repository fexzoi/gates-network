# GTS - GATES NETWORK AI Moderation (staging)

This service is the first, isolated backend component for marketplace moderation. It is intentionally **not connected to Wix writes or publication** yet.

## What it does

- Authenticated `POST /v1/moderate` endpoint.
- Calls the OpenAI Responses API using a strict JSON Schema output.
- Applies a conservative approval threshold: an approval with risk score >= 35 or confidence < 75 is changed to rejection.
- Logs every completed decision and provider failure with timestamp, model, decision, score, reasons and policy flags.
- Authenticated `GET /v1/audit` endpoint.
- Repeated completed submissions are idempotent by `submission_id`.
- Provider/configuration failures fail closed; no approval is returned and publication remains disabled.

## Explicit safety boundary

`published` is always `false`. This service does not call Wix, does not update either CMS collection, and cannot activate or publish a listing. A future integration must authenticate server-to-server, confirm the submission's actual Wix record and current status, validate the AI result, and only then implement a separately tested publish step. Do not enable publication until integration and staging tests pass.

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
- `MODERATION_DB_PATH`: SQLite path. SQLite is suitable for local/staging tests; for production on ephemeral hosting, use persistent storage or managed PostgreSQL before relying on audit retention.

## API

- `GET /health` — public liveness check; reports that publication is disabled.
- `POST /v1/moderate` — requires `Authorization: Bearer <MODERATION_API_TOKEN>` and JSON body with `submission_id`, `title`, `platform`, `category`, `description`, `price_text`, `seller_name`, `seller_contact_url`.
- `GET /v1/audit?limit=50` — same authorization; optional `submission_id` filter.

## Tests

```bash
pytest -q
```

Do not point the seller form at this service until the Wix server-to-server integration and end-to-end tests are completed. This first commit is a staging-only component.
