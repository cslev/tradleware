# Building & Developing Tradleware

This document covers building the Docker image and running Tradleware locally for development.

---

## Building the Docker Image

Tradleware uses **Docker Buildx** for multi-architecture builds (amd64 + arm64), so the same image works on both x86 servers and Raspberry Pi.

### One-time setup

```bash
docker buildx create --name multiarch --driver docker-container --use
docker buildx inspect --bootstrap
docker run --privileged --rm tonistiigi/binfmt --install arm64
```

### Build and push to Docker Hub (multi-arch)

Replace `vX.Y` with the new version tag:

```bash
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  --tag cslev/tradleware:latest \
  --tag cslev/tradleware:vX.Y \
  --push \
  .
```

> `--push` is required for multi-arch builds — they cannot be loaded to the local Docker daemon.

### Test locally (single-arch, no push)

```bash
docker buildx build --platform linux/amd64 --tag cslev/tradleware:latest --load .
```

---

## Before Building — Version Bump Checklist

1. Update `TRADLEWARE_VERSION` in `src/ui/app.py`
2. Update `.github/current_state.md` with the new version
3. Commit and push the version bump before building

---

## Running Locally for Development

### Prerequisites

- Python 3.11+
- Node.js (for Tailwind CSS)

### Setup

```bash
cd /path/to/tradleware
python -m venv .venv
source .venv/bin/activate
pip install -r src/requirement.txt
```

### Start Tailwind CSS watcher (separate terminal)

```bash
cd src/ui
npx tailwindcss -i ./static/css/input.css -o ./static/css/output.css --watch
```

### Run the FastAPI app (project root)

```bash
uvicorn src.ui.app:app --host 0.0.0.0 --port 8080 --reload --no-proxy-headers
```

The app will be available at `http://localhost:8080`.

> **`--no-proxy-headers` is not optional.** Uvicorn's proxy-header handling is on by
> default and rewrites the client address from `X-Forwarded-For` for peers it trusts
> (`127.0.0.1` unless told otherwise). Tradleware resolves the client address itself via
> `TRUSTED_PROXIES`, and that check is meaningless if uvicorn already overwrote the value
> it inspects — a request from loopback could then claim any address it liked. The
> bundled `Dockerfile` passes this flag for the same reason.

> When running outside Docker, set `host: "127.0.0.1"` in `bot_configs/stock/ibkr.yaml` instead of `ib_gateway`.

---

## Code Quality

Always run pylint before committing and ensure score is **10.00/10**:

```bash
pylint src/ | grep -v "E0401"
```
