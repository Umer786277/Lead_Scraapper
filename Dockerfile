# Use the official Playwright Python image — has Chromium + all OS deps pre-installed.
# This saves ~400 MB vs installing them manually in python:slim.
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright browsers are pre-installed in the base image.
# Run this only to make sure the version matches requirements.txt.
RUN playwright install chromium

# Copy API and worker source
COPY . .

# FastAPI runs the background worker via api.worker_runner.
ENV PORT=8000

EXPOSE $PORT

CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT}"]
