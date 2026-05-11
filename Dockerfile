# Use the official Playwright Python image — has Chromium + all OS deps pre-installed.
# This saves ~400 MB vs installing them manually in python:slim.
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install supervisord to run Streamlit + worker in one container
RUN pip install --no-cache-dir supervisor

# Playwright browsers are pre-installed in the base image.
# Run this only to make sure the version matches requirements.txt.
RUN playwright install chromium

# Copy app source
COPY . .

# Streamlit binds to $PORT (set by Railway). Default 8501 for local Docker.
ENV PORT=8501

EXPOSE $PORT

CMD ["supervisord", "-c", "supervisord.conf", "-n"]
