FROM python:3.11-slim

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code — backend FastAPI + the agent/skills/rules packages it imports
# (these used to live in a sibling `concurshield-agent/` repo before being
# vendored into the project root)
COPY backend/    ./backend/
COPY agent/      ./agent/
COPY skills/     ./skills/
COPY rules/      ./rules/
COPY models/     ./models/
COPY config/     ./config/
COPY mock_data/  ./mock_data/
COPY frontend/   ./frontend/

# Create upload dir
RUN mkdir -p /app/uploads

EXPOSE 8000

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
