# ──────────────────────────────────────────────────────────────────
# Grid-Guardian RL — CPU Dockerfile
# ──────────────────────────────────────────────────────────────────
FROM python:3.10-slim

WORKDIR /app

# system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential git && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# default: train CQL offline
CMD ["python", "train_rl_agents.py", \
     "--config", "configs/train_rl.yaml", \
     "--algo", "CQL", "--mode", "offline", "--seed", "42"]
