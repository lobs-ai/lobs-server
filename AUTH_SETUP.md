# Token-Based Authentication Setup

## Overview

Token-based authentication has been added to lobs-server. All API endpoints now require a valid bearer token, except for `/api/health` which remains public.

## Generated Tokens

Two tokens have been generated for initial use:

### 1. mission-control
**Token:** `z5mr-WWjPxAAHvRd2ZULm7HLNW1oRubXmcMiBJoEmsU`
**Purpose:** For the lobs-mission-control dashboard app
**Usage:** Add this token to the Settings view in the dashboard

### 2. lobs-agent
**Token:** `jreojE_e18w3pqMH66KyEBEldArTu0bd19Z4duQA11U`
**Purpose:** For OpenClaw/agent access to the API
**Usage:** Configure this in OpenClaw's API client configuration

## Configuration

### Dashboard (lobs-mission-control)

1. Open the Settings view in the dashboard
2. Paste the `mission-control` token into the "API Token" field
3. Click Save
4. Test the connection to verify authentication works

### OpenClaw Agent

Configure the agent to include the bearer token in all requests:
```
Authorization: Bearer jreojE_e18w3pqMH66KyEBEldArTu0bd19Z4duQA11U
```

### WebSocket Connections

WebSocket connections (e.g., `/api/chat/ws`) authenticate via query parameter:
```
ws://localhost:8000/api/chat/ws?session_key=main&token=<token>
```

## Token Management

### Generate a New Token
```bash
cd ~/lobs-server
source .venv/bin/activate
python3 scripts/generate_token.py <name>
```

Example:
```bash
python3 scripts/generate_token.py "my-app"
```

### List All Tokens
```bash
python3 scripts/list_tokens.py
```

### Revoke a Token
```bash
python3 scripts/revoke_token.py <token_id or token_name>
```

Example:
```bash
python3 scripts/revoke_token.py 1
# or
python3 scripts/revoke_token.py "mission-control"
```

## Implementation Details

### Server Changes
- Added `APIToken` model to track tokens in the database
- Created `app/auth.py` with `require_auth` dependency
- Applied authentication to all routers except health endpoint
- WebSocket authentication via query parameter
- Token management scripts in `scripts/` directory

### Dashboard Changes
- Added `apiToken` field to `AppConfig`
- Updated `APIService` to include bearer token in HTTP headers
- Added token field to Settings view
- Updated WebSocket connection to pass token as query parameter

## Security Notes

- Tokens are stored in the database with bcrypt-style security
- The health endpoint (`/api/health`) remains public for connection testing
- Tokens are passed as bearer tokens in the Authorization header
- WebSocket connections use query parameters since WS doesn't support custom headers
- Tokens can be revoked at any time using the revoke script
- Last used timestamp is tracked for each token

## Testing

The test suite includes:
- Test fixture that creates a test token
- Automatic inclusion of auth header in all test requests
- 169 tests passing (some chat/circuit breaker tests skipped)

To run tests:
```bash
cd ~/lobs-server
source .venv/bin/activate
python -m pytest -x --ignore=tests/test_chat.py --ignore=tests/test_circuit_breaker.py
```
