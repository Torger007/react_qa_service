# ReAct QA Service

FastAPI-based, extensible Q&A service designed with a ReAct-style engine scaffold.

## Features

- REST API with OpenAPI 3.0, JSON request/response
- Swagger UI at `/swagger`
- Session state in Redis: keep latest 10 rounds (20 messages) per session
- Session ID: UUIDv4
- Security
  - JWT authentication
  - Rate limit: 5 requests/second
  - Sensitive action confirmation (two-step)

## Project layout

Matches the required structure under `react-qa-service/`.

## Run locally (Docker)

1. Create `.env` from example:

```bash
cp .env.example .env
```

2. Start:

```bash
docker compose up --build
```

3. Open Swagger:

- `http://localhost:8000/swagger`

## Frontend (React + Vite)

For a simple web chat UI:

```bash
npm install
npm run dev
```

Then open `http://localhost:5173` and:

- Log in with the demo account (`admin` / `admin` by default).
- Drag files (`.txt/.md/.csv/.json/.log/.pdf/.docx`) into the chat input area to upload and index docs.
- Ask questions, which will be sent to `/api/v1/chat/qa` with JWT auth.

If your frontend runs on a different dev server/port (e.g. `3000`) and no proxy is configured,
set `VITE_API_BASE_URL=http://localhost:8000` in frontend env.

## Document ingestion APIs

- `POST /api/v1/docs/index` for raw text JSON payload.
- `POST /api/v1/docs/upload` for multipart file upload (field `file`, optional `doc_id`, `metadata_json`).

## API quickstart

### 1) Get JWT

`POST /api/v1/auth/login`

Body:

```json
{ "username": "admin", "password": "admin" }
```

### 2) Chat

`POST /api/v1/chat/` with header `Authorization: Bearer <token>`

Body:

```json
{ "message": "你好，介绍一下ReAct是什么？" }
```

### 3) Sensitive action confirmation

First call (no `confirm_token`) returns **202** with `confirm_token`:

```json
{ "message": "我要执行删除", "action": "delete", "action_input": { "id": "123" } }
```

Second call includes `confirm_token` to proceed:

```json
{
  "message": "确认执行删除",
  "session_id": "<same-session-id>",
  "action": "delete",
  "confirm_token": "<confirm-token-from-202>"
}
```

## Quality & Testing

This project is a **FastAPI (Python)** service. The repository also provides a `package.json` as a **single command entrypoint**
for quality checks (lint/format/typecheck/tests), so CI can call one consistent command set.

### Install dev dependencies

Create a virtualenv and install:

```bash
python -m pip install -r requirements.txt
```

### Run tests

```bash
python -m pytest -q
```

Auth-only:

```bash
python -m pytest -q tests/test_auth.py
```

Chat-only:

```bash
python -m pytest -q tests/test_chat.py
```

### Quality command entrypoint (npm scripts)

If you prefer a unified entrypoint:

```bash
npm run validate
```

Available scripts:

- `npm run lint` / `npm run lint:fix` (ruff)
- `npm run format` / `npm run format:check` (black)
- `npm run typecheck` (mypy)
- `npm run test` / `npm run test:auth` / `npm run test:chat`
- `npm run test:coverage` (pytest-cov; outputs `htmlcov/`)

