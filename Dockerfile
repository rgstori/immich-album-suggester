# Dockerfile
FROM python:3.11-slim

WORKDIR /usr/src/app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Create the data directory for SQLite database
RUN mkdir -p /usr/src/app/data

# Copy the configuration and the application code
COPY config.yaml ./
COPY ./app ./app
COPY ui.py ./

# The default command can be the UI, as it's the main entrypoint
CMD ["streamlit", "run", "ui.py", "--server.port=8501", "--server.address=0.0.0.0"]