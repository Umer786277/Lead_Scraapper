# Lead Scraper

Lead Scraper is a lead-generation application with a FastAPI backend, background
workers, and a Next.js frontend.

## Project layout

```text
api/          FastAPI application and API routers
data/         Local databases and generated data (not versioned)
frontend/     Next.js web application
scripts/      Database migrations and one-off administrative commands
tests/        Automated Python tests
*.py          Backend services and worker entry points
```

Backend modules remain at the repository root because the API, workers, and
tests currently import them by their root-level module names.

## Local development

Install the Python dependencies and start the API from the repository root:

```powershell
python -m pip install -r requirements.txt
python -m uvicorn api.main:app --reload --host 0.0.0.0
```

Run the background workers in separate terminals when needed:

```powershell
python worker.py
python worker_replies.py
```

Install and run the frontend from its directory:

```powershell
Set-Location frontend
npm install
npm run dev
```

Configure required backend and frontend environment variables before running
the application. Keep local `.env` files and credentials out of version
control.

## Tests

Run the automated Python suite from the repository root:

```powershell
python -m pytest tests
```

## Outreach templates

Organizations can add any number of named email scripts with their own subject
and body. When using **Email now**, users must explicitly select the script to
send. Campaigns use saved campaign automations, with script variants and their
shared follow-up delays configured together. Campaign steps snapshot their
email copy and schedule when the campaign is created, so later edits do not
change already-scheduled messages. Replies, unsubscribes, bounces,
and suppressed recipients stop pending follow-ups.
New leads are no longer automatically enrolled in the legacy four-step
**Auto Outreach** flow. Emails already queued by that flow are left untouched
and continue through the existing send queue.

Campaign automations remain available for campaigns using inline script
variants. When the Leads builder uses automatic script bundles, all eligible
four-step variants from the organization's campaign automations form one
selection pool; the user does not select an individual automation. Bundles
must share the same four step delays. At enrollment the organization-wide
**Sequential** round-robin or **Random** shuffled-cycle setting assigns and
snapshots one bundle per lead. The scheduler automatically queues all four
steps at their configured intervals using that saved copy for retries and
follow-ups. Later mode changes do not alter existing assignments.

The Leads builder also supports attaching multiple saved four-step sequences
(Initial -> Bump -> Value -> Break-up). Selected sequences must share their
follow-up delays; one sequence and its copy snapshot are assigned per lead at
enrollment using the same organization selection setting. Existing lead
assignments are preserved when campaign options are updated.

The template-option feature requires the additive
`scripts/migrate_campaign_template_options.py` migration before it is enabled.
It is intentionally not run automatically during application startup. Review
and apply it as a separate, explicitly approved database operation. The
development database has already had this migration applied.

## Maintenance commands

Maintenance commands are Python modules under `scripts/`. Run them from the
repository root, for example:

```powershell
python -m scripts.migrate_sqlite_to_pg --dry-run
python -m scripts.refresh_templates
```

The `scripts.send_test_call` and `scripts.send_test_email` commands perform
real external actions; only run them with an explicitly chosen test target.

Generated browser downloads, local state, logs, caches, and local data files
are intentionally excluded from source control and Docker build contexts.
