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
python -m uvicorn api.main:app --reload
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
send. Admins can build reusable sequences
with one initial email and up to three optional follow-ups, configure the delay
between steps, and select a saved sequence when creating a campaign. Campaign
steps snapshot their email copy when the campaign is created, so later template
edits do not change already-scheduled messages. Replies, unsubscribes, bounces,
and suppressed recipients stop pending follow-ups.
New leads are no longer automatically enrolled in the legacy four-step
**Auto Outreach** flow. Emails already queued by that flow are left untouched
and continue through the existing send queue.

Campaigns can optionally use a saved set of up to five complete script
variants. The organization-wide **Sequential** or **Random** setting applies
to every script set. Sequential rotates through variants in order; Random
shuffles a cycle so every variant is used once before any repeats. The next
cycle avoids starting with the previous cycle's last variant when there is
more than one variant. Rotation state is persisted per campaign. Changing the
setting also affects unassigned leads in existing campaigns, while each lead's
saved assignment remains unchanged. Variants must have the same number of steps
as the campaign sequence; the selected sequence continues to control follow-up
timing. A lead's variant is snapshotted when its first email is claimed for sending, so
retries and follow-ups keep the same copy. Editing a set affects only leads
that have not yet been assigned a variant. Campaigns without a selected set
keep using their sequence's existing templates.

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
