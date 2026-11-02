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
