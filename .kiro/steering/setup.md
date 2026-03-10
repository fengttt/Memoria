---
inclusion: always
---

# TrustMem Local Setup

When the user wants to set up TrustMem, **do NOT jump straight into installation**. First ask key questions to determine the right path.

## Decision Flow

### Question 1: Which AI tool?
Ask: "You're using Kiro, Cursor, or Claude Code? (or multiple?)"
This determines which config files to generate.

### Question 2: MatrixOne database
Ask: "Do you already have a MatrixOne database running? If not, I can help you set one up. You have two options:
1. **Local Docker** (recommended for development) — I'll start one for you with docker-compose
2. **MatrixOne Cloud** (free tier available) — register at https://cloud.matrixorigin.cn, no Docker needed"

Based on the answer:
- **Already have one** → ask for the connection URL (host, port, user, password, database)
- **Local Docker** → follow Docker setup below
- **MatrixOne Cloud** → guide user to register, then get connection URL from console

### Question 3: Embedding provider

**⚠️ WARN THE USER BEFORE PROCEEDING**: `[local-embedding]` pulls `sentence-transformers` + `torch`, which is **~900MB**. On slow or proxied networks this will time out.

Ask: "For memory search quality, TrustMem needs an embedding model. Options:
1. **Existing service** (recommended if available) — OpenAI, Ollama, or any embedding endpoint you already run. No download, no cold-start. **Best choice if you have one.**
2. **OpenAI** — better quality, needs API key, no cold-start delay, no large download.
3. **Local** (free, private) — ⚠️ downloads ~900MB (torch + sentence-transformers) on first install. **Avoid on slow/proxied networks.**"

**If user chooses local embedding, explicitly warn**: "This will download ~900MB. If you're on a slow or proxied network, consider using OpenAI or an existing embedding service instead. Proceed?"

## Execution Rules

**CRITICAL: Execute commands one at a time, never chain unrelated steps.**

- Run each command separately and wait for success before proceeding
- If a command fails, stop and diagnose before continuing
- Never chain install + configure + verify into one shell call

## Execution Paths

### Path A: Local Docker + Local Embedding (most common)

```bash
# Step 1: Start MatrixOne (run alone, check output)
docker compose up -d
# or:
docker run -d --name matrixone -p 6001:6001 -v ./data/matrixone:/mo-data --memory=2g matrixorigin/matrixone:latest
```
Wait for success, then:
```bash
# Step 2: Verify MatrixOne is running
docker ps --filter name=matrixone
```
Wait ~30-60s on first start, then:
```bash
# Step 3: Create virtual environment (run alone)
python3 -m venv .venv
```
```bash
# Step 4: Activate it (run alone)
source .venv/bin/activate
```
```bash
# Step 5: Install TrustMem (run alone — this may take a while if using local-embedding)
pip install --index-url https://pypi.org/simple/ --extra-index-url https://test.pypi.org/simple/ 'trust-mem-lite[local-embedding]'
```
```bash
# Step 6: Configure (in user's project directory)
cd <user-project>
trustmem init
```

### Path B: MatrixOne Cloud

```bash
# 1. User registers at https://cloud.matrixorigin.cn (free tier)
# 2. Get connection info from cloud console: host, port, user, password

# 3. Virtual environment
python3 -m venv .venv
```
```bash
source .venv/bin/activate
```
```bash
# 4. Install
pip install --index-url https://pypi.org/simple/ --extra-index-url https://test.pypi.org/simple/ 'trust-mem-lite[local-embedding]'
```
```bash
# 5. Configure with cloud URL
cd <user-project>
trustmem init --db-url 'mysql+pymysql://<user>:<password>@<host>:<port>/<database>'
```

### Path C: Existing MatrixOne

```bash
# 1. Virtual environment
python3 -m venv .venv
```
```bash
source .venv/bin/activate
```
```bash
# 2. Install
pip install --index-url https://pypi.org/simple/ --extra-index-url https://test.pypi.org/simple/ 'trust-mem-lite[local-embedding]'
```
```bash
# 3. Configure with existing DB
cd <user-project>
trustmem init --db-url 'mysql+pymysql://<user>:<password>@<host>:<port>/<database>'
```

### Embedding provider flags (for any path)

```bash
# Local (default) — no extra flags needed
trustmem init

# OpenAI
trustmem init --embedding-provider openai --embedding-api-key sk-...

# Existing service (Ollama, custom endpoint, etc.)
trustmem init --embedding-provider openai --embedding-base-url http://localhost:11434/v1
```

## After any path

```bash
# Verify
trustmem status

# Tell user to restart their AI tool
```

## Troubleshooting
- MatrixOne won't start → `docker logs trustmem-matrixone` to check errors
- Port 6001 in use → edit `.env` to change `MO_PORT`, then `docker compose up -d`
- Can't connect to DB → MatrixOne needs 30-60s on first start, wait and retry
- Cloud connection refused → check firewall/whitelist settings in cloud console
- **Docker permission denied** → `sudo usermod -aG docker $USER && newgrp docker`
- **Image pull slow/timeout** → configure Docker mirror in `/etc/docker/daemon.json`, add `"registry-mirrors": ["https://docker.1ms.run"]`, then `sudo systemctl restart docker`
- **Docker not installed** → suggest MatrixOne Cloud (https://cloud.matrixorigin.cn) as alternative, no Docker needed
- **Data dir permission error** → `mkdir -p data/matrixone && chmod 777 data/matrixone`
- **First query slow** → expected with local embedding; model loads into memory on first use (~3-5s). Subsequent queries are fast. Use `--embedding-provider openai` to avoid this.
