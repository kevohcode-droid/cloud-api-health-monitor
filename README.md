# Cloud API Health Monitor

A lightweight Python service for monitoring HTTP endpoints and storing health-check results in PostgreSQL. The app exposes a FastAPI interface for creating monitors, running checks, and reviewing historical results.

## Overview

This project is designed to help teams track the availability and responsiveness of external API endpoints. It can:

- Check a URL asynchronously using FastAPI and httpx
- Record metrics such as HTTP status code, latency, and health outcome
- Persist monitor metadata and check history in PostgreSQL
- Send Slack outage and recovery alerts after configurable consecutive failures
- Run locally with Docker Compose
- Be extended for alerting, dashboards, and cloud deployment workflows

## Tech stack

- Python 3.12+
- FastAPI
- SQLAlchemy (async)
- PostgreSQL
- Docker / Docker Compose
- pytest
- Terraform scaffold for AWS resources
- GitHub Actions workflow support

## Project structure

```text
cloud-api-health-monitor/
├── app/
│   ├── main.py
│   ├── config.py
│   ├── database.py
│   ├── logging_config.py
│   ├── models/
│   ├── monitoring/
│   └── schemas/
├── tests/
├── terraform/
├── .env.example
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── README.md
└── .gitignore
```

## Prerequisites

- Python 3.12+
- Docker and Docker Compose
- PostgreSQL (or the included PostgreSQL service in Docker Compose)

## Local setup

1. Clone the repository.
2. Copy the example environment file:

   ```bash
   cp .env.example .env
   ```

3. Update the values in `.env` if needed. Example:

   ```env
   DATABASE_URL=postgresql+asyncpg://healthmonitor:change-me-locally@localhost:5432/healthmonitor
   APP_NAME=Cloud API Health Monitor
   APP_VERSION=0.1.0
   ```

4. Start PostgreSQL locally:

   ```bash
   docker compose up -d db
   ```

5. Create a virtual environment and install dependencies:

   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```

6. Run the application:

   ```bash
   uvicorn app.main:app --reload
   ```

The API will be available at `http://localhost:8000`.

## API endpoints

### Health

```http
GET /health
```

Returns a basic service status payload:

```json
{"status": "ok"}
```

### Run a one-off check

```http
GET /check?url=https://example.com&timeout=5.0
```

Returns a result like:

```json
{
  "url": "https://example.com",
  "status_code": 200,
  "latency_ms": 123.45,
  "is_healthy": true,
  "error": null
}
```

### Monitor management

```http
POST /monitors
GET /monitors
POST /monitors/{monitor_id}/checks
GET /monitors/{monitor_id}/checks
```

- `POST /monitors`: create a monitor definition with a URL and metadata
- `GET /monitors`: list all configured monitors
- `POST /monitors/{monitor_id}/checks`: trigger a health check for a monitor
- `GET /monitors/{monitor_id}/checks`: read historical check results for a monitor

## Database model

The app stores:

- `Monitor`: a target URL and monitor metadata
- `HealthCheck`: the outcome of a health probe for a given monitor
- `MonitorAlertState`: current health transition state and failure streak per monitor

These models are mapped with SQLAlchemy and persisted in PostgreSQL.

The `monitor_alert_states` table is created automatically by the existing
`Base.metadata.create_all()` startup initialization. Existing tables are not
altered. For deployments that manage schema outside application startup, create
the equivalent table before deploying:

```sql
CREATE TABLE monitor_alert_states (
    monitor_id INTEGER PRIMARY KEY REFERENCES monitors(id) ON DELETE CASCADE,
    status VARCHAR(16) NOT NULL DEFAULT 'healthy',
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    failure_streak_started_at TIMESTAMPTZ NULL,
    outage_started_at TIMESTAMPTZ NULL,
    recovery_started_at TIMESTAMPTZ NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

## Slack alerts

Create an incoming webhook in Slack:

1. In the Slack API dashboard, create or select an app for your workspace.
2. Under **Incoming Webhooks**, enable the feature and choose **Add New Webhook
   to Workspace**.
3. Select the channel and authorize the app. Copy the generated webhook URL.
4. Supply it as `SLACK_WEBHOOK_URL`. Treat it like a password: do not commit it,
   paste it into source control, or include it in logs or support requests.

The application reads `SLACK_WEBHOOK_URL` and `ALERT_FAILURE_THRESHOLD` from its
environment (or the local `.env` file). The threshold defaults to `3`. If the
webhook is missing, checks continue normally and one warning is logged per
application process. Slack sends use a five-second request timeout and retry
timeouts, transport errors, HTTP 429, and HTTP 5xx up to three times with
exponential backoff; rate limits honor `Retry-After` up to 60 seconds. A failed
send is logged without printing the webhook URL and does not fail the check.
The first successful check after an outage sends the recovery alert and moves
the persisted state to `recovering`; the following successful check confirms
the monitor as `healthy`. A failure after recovery starts a fresh failure
streak.

### Windows PowerShell end-to-end test

These steps use a local controllable HTTP target; no public website is involved.
Run the database, API, and target in separate PowerShell windows, all from the
project directory.

1. In the API window, enter the webhook through a secure prompt, start
   PostgreSQL, and run the API. The webhook is held only in this shell's process
   environment and is not added to `.env` or shell history:

   ```powershell
   cd C:\PROJECTS\DEVOPS\cloud-api-health-monitor
   $secret = Read-Host "Slack incoming webhook URL" -AsSecureString
   $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
   try {
       $env:SLACK_WEBHOOK_URL = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
   } finally {
       [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
       $secret.Dispose()
   }
   docker compose up -d db
   .\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
   ```

   Keep the API running in this window so it retains the environment value.
   Stop it with Ctrl+C when the test is complete, then clear the value with
   `Remove-Item Env:SLACK_WEBHOOK_URL`.

2. In a second PowerShell window, run the local test endpoint:

   ```powershell
   cd C:\PROJECTS\DEVOPS\cloud-api-health-monitor
   .\.venv\Scripts\python.exe .\tests\slack_test_target.py
   ```

3. In a third PowerShell window, register a monitor with a 10-second interval
   (the minimum accepted interval):

   ```powershell
   $monitor = Invoke-RestMethod -Method Post `
     -Uri http://localhost:8000/monitors `
     -ContentType "application/json" `
     -Body (@{
       name = "Local Slack alert test"
       url = "http://127.0.0.1:8765/health"
       interval_seconds = 10
       enabled = $true
     } | ConvertTo-Json)
   $monitorId = $monitor.id
   ```

4. Set the target unhealthy. Wait for three failed checks (about 30 seconds at
   the default threshold), or trigger checks manually to shorten the test:

   ```powershell
   Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8765/fail
   1..3 | ForEach-Object {
     Invoke-RestMethod -Method Post `
       -Uri "http://localhost:8000/monitors/$monitorId/checks"
   }
   ```

   Confirm one outage alert arrives in the chosen Slack channel. Continuing to
   fail the target and triggering more checks must not create duplicate outage
   alerts.

5. Restart the API (Ctrl+C, then rerun the uvicorn command in step 1; set the
   secure environment value again if using a new shell). With the target still
   failing, trigger another check and confirm there is no duplicate outage
   alert. This verifies that state survived the application restart.

6. Restore the target and trigger one successful check:

   ```powershell
   Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8765/recover
   Invoke-RestMethod -Method Post `
     -Uri "http://localhost:8000/monitors/$monitorId/checks"
   ```

   Confirm the recovery alert arrives. Its outage duration is included only
   when the persisted outage start time was available and valid.

To clean up, stop the local target and API with Ctrl+C. The monitor and alert
state remain in the local PostgreSQL volume; remove test data through the API or
the database if desired.

## Running tests

```bash
pytest
```

The test suite includes checks for healthy and unhealthy responses, timeout handling, invalid domains, and FastAPI endpoint behavior.

## Docker

The repository includes a Docker Compose definition for PostgreSQL and a Dockerfile for building the application container. For example:

```bash
docker build -t cloud-api-health-monitor .
docker run -p 8000:8000 --env-file .env cloud-api-health-monitor
```

## Terraform and cloud deployment

The `terraform/` directory contains a starting point for AWS infrastructure provisioning. It is intended as a foundation for deployment, storage, or environment setup and can be extended to match production deployment requirements.

## Notes

- The monitoring logic is intentionally resilient: a failing URL or timeout returns a structured error result instead of crashing the service.
- The service is designed to be extended with alerting, queue-based workers, or scheduled checks as the monitoring requirements grow.

## License

This project is currently distributed without a formal license file. Add a license if you plan to share or publish the repository publicly.
