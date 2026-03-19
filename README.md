# llm-docker-sandbox

A FastAPI-based REST API service for creating and managing isolated sandbox environments that safely execute arbitrary shell commands in containerized, resource-limited environments.

## Overview

`llm-docker-sandbox` lets you spin up ephemeral sandboxes, upload files into them, and execute shell commands — all over a simple HTTP API. Each command runs in a fresh temporary directory with enforced memory, file descriptor, and time limits so untrusted code can't escape or exhaust your host.

**Ideal for:**
- Code execution as a service (e.g. educational platforms, online judges)
- Running untrusted or user-submitted scripts safely
- Automated testing and benchmarking in isolation
- CI pipelines that need throwaway execution environments

## Architecture

```
Client Request
    │
    ▼
FastAPI (main.py)         ← Input validation via Pydantic models
    │
    ├── POST /sandbox      → SandboxManager: creates UUID directory, stores files
    ├── GET  /sandbox/:id  → SandboxManager: lists files
    ├── POST /sandbox/:id/exec
    │       │
    │       ▼
    │   WorkerPool         ← Async task queue + thread pool executor
    │       │
    │       ▼
    │   TemporaryDirectory ← Fresh copy of sandbox files per execution
    │       │
    │       ▼
    │   subprocess (bash)  ← Resource-limited, timeout-enforced
    │       │
    │       ▼
    │   ExecResult         ← stdout, stderr, exit_code, error
    │
    └── DELETE /sandbox/:id → SandboxManager: removes directory
```

**Key design principles:**
- **Immutability**: Each command runs against a fresh copy of sandbox files. Mutations (e.g. writing new files) do not persist between executions.
- **Isolation**: Per-execution temp directories prevent cross-contamination.
- **Resource limits**: Memory capped at 256 MB (configurable), file descriptors at 256, and a configurable timeout per command.

## API Reference

### Health Check

```
GET /health
```

**Response:**
```json
{ "status": "ok", "workers": 4, "queue_size": 0 }
```

---

### Create Sandbox

```
POST /sandbox
```

**Request body:**
```json
{
  "files": [
    { "name": "hello.py",       "content": "print('hello')" },
    { "name": "data/input.txt", "content": "SGVsbG8K", "encoding": "base64" }
  ]
}
```

| Field              | Type   | Required | Description                                     |
|--------------------|--------|----------|-------------------------------------------------|
| `files`            | array  | No       | Files to pre-populate the sandbox with          |
| `files[].name`     | string | Yes      | Relative file path (no `..` or absolute paths)  |
| `files[].content`  | string | Yes      | File content (text or base64-encoded)           |
| `files[].encoding` | string | No       | `"text"` (default) or `"base64"`               |

**Response** `201 Created`:
```json
{ "id": "3f2a1c8d-..." }
```

---

### Get Sandbox Info

```
GET /sandbox/{sandbox_id}
```

**Response:**
```json
{ "id": "3f2a1c8d-...", "files": ["hello.py", "data/input.txt"] }
```

---

### Execute a Command

```
POST /sandbox/{sandbox_id}/exec
```

**Request body:**
```json
{
  "command": "python3 hello.py",
  "timeout": 30
}
```

| Field     | Type    | Required | Description                            |
|-----------|---------|----------|----------------------------------------|
| `command` | string  | Yes      | Shell command to run (executed via bash) |
| `timeout` | integer | No       | Timeout in seconds (default: `EXEC_TIMEOUT`) |

**Response:**
```json
{
  "stdout": "hello\n",
  "stderr": "",
  "exit_code": 0,
  "error": null
}
```

If the command times out, `error` will be `"timeout"` and `exit_code` will be `-1`.

---

### Delete Sandbox

```
DELETE /sandbox/{sandbox_id}
```

**Response:**
```json
{ "id": "3f2a1c8d-...", "deleted": true }
```

---

## Quickstart

### Docker Compose (recommended)

```bash
git clone <repo-url>
cd llm-docker-sandbox
docker compose up --build
```

The API is available at `http://localhost:8000`. Interactive docs at `http://localhost:8000/docs`.

### Example session

```bash
# Create a sandbox with a Python script
SB=$(curl -s -X POST http://localhost:8000/sandbox \
  -H 'Content-Type: application/json' \
  -d '{"files":[{"name":"hello.py","content":"print(\"hello world\")"}]}' \
  | jq -r .id)

# Execute the script
curl -s -X POST http://localhost:8000/sandbox/$SB/exec \
  -H 'Content-Type: application/json' \
  -d '{"command":"python3 hello.py"}'
# → {"stdout":"hello world\n","stderr":"","exit_code":0,"error":null}

# Clean up
curl -s -X DELETE http://localhost:8000/sandbox/$SB
```

---

## Local Development

### Prerequisites

- Python 3.12+
- pip

### Setup

```bash
pip install -r requirements-dev.txt
```

### Run the API

```bash
cd app
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### Run Tests

```bash
pytest                          # run all tests
pytest -v                       # verbose output
pytest --cov=app --cov-report=term-missing   # with coverage report
pytest tests/test_api.py        # integration tests only
pytest tests/test_sandbox.py    # unit tests: SandboxManager
pytest tests/test_worker.py     # unit tests: WorkerPool
```

---

## Configuration

All settings are controlled via environment variables:

| Variable      | Default       | Description                                      |
|---------------|---------------|--------------------------------------------------|
| `NUM_WORKERS` | `4`           | Number of concurrent async workers               |
| `EXEC_TIMEOUT`| `30`          | Default command timeout in seconds               |
| `MEM_LIMIT_MB`| `256`         | Memory limit per execution in megabytes (Linux)  |
| `SANDBOX_DIR` | `/sandboxes`  | Path where sandbox directories are stored        |

---

## Security

- **Path traversal prevention**: File names are sanitized to reject `../` sequences and absolute paths.
- **Resource limits**: Each command's virtual memory is capped (`MEM_LIMIT_MB`) and open file descriptors are limited to 256 (Linux only via `resource.setrlimit`).
- **Immutable sandboxes**: Sandbox file state is never mutated by executions — each run gets a clean copy.
- **Non-root execution**: The container runs as `sandboxuser` (UID 1000), never root.
- **Dropped capabilities**: `docker compose` drops all Linux capabilities (`cap_drop: ALL`) and sets `no-new-privileges: true`.
- **Timeout enforcement**: Every command has a hard timeout; hung processes are killed automatically.

> **Note:** These measures provide strong isolation for most use cases but are not a substitute for a fully hardened sandbox (e.g., gVisor, Firecracker) when executing highly adversarial code.

---

## Project Structure

```
llm-docker-sandbox/
├── app/
│   ├── main.py          # FastAPI application, routes, Pydantic models
│   ├── sandbox.py       # SandboxManager: filesystem isolation & file storage
│   └── worker.py        # WorkerPool: async queue, subprocess execution, resource limits
├── tests/
│   ├── conftest.py      # Shared pytest fixtures (app client, temp sandboxes)
│   ├── test_api.py      # End-to-end API integration tests
│   ├── test_sandbox.py  # Unit tests for SandboxManager
│   └── test_worker.py   # Unit tests for WorkerPool
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── requirements-dev.txt
└── pytest.ini
```

---

## Tech Stack

| Layer         | Technology                        |
|---------------|-----------------------------------|
| Web framework | FastAPI 0.115+                    |
| ASGI server   | Uvicorn (standard)                |
| Validation    | Pydantic v2                       |
| Runtime       | Python 3.12                       |
| Container     | Docker (python:3.12-slim)         |
| Testing       | pytest, pytest-asyncio, httpx     |

---

## License

MIT
