# Connector SDK — Design & MVP Specification

**Status:** Proposed MVP  
**Date:** 2026-02-25  
**Author:** Lobs Writer  
**Initiative:** a92914ed-98fe-4cd9-ad6c-ef5275d5c3d2

---

## Overview

The Connector SDK defines a lightweight, stable contract for adding third-party service integrations to lobs-server. New connectors — Discord, Notion, Slack, Linear, or anything else — ship as isolated packages without touching orchestrator internals.

**What this solves:**

Currently each integration (GitHub, Google Calendar, Email) is a bespoke service class written directly into `app/services/`. Adding a new integration means:
- Learning internal data models from scratch
- Choosing your own auth strategy
- Writing your own webhook handling
- No standard for how data normalizes into tasks, memories, or calendar events

The result is slow onboarding, integration bottlenecks, and fragile code.

**The SDK approach:**

Define a stable interface once. Each connector implements that interface. The registry loads connectors at startup. The orchestrator calls the interface — not the connector's internals.

---

## Audience

- **Programmers** adding official connectors to lobs-server
- **External contributors** building connectors for their own tools
- **The orchestrator / agent system** consuming connector data

---

## Connector Interface

Every connector implements four capabilities. Unused capabilities return a standard `NotSupported` result.

### 1. Auth

How the connector authenticates with the external service.

```
authenticate(config: ConnectorConfig) -> AuthResult
refresh_token(credentials: Credentials) -> Credentials
validate_credentials(credentials: Credentials) -> bool
```

- `config` contains all env vars and settings the connector declared in its manifest
- `credentials` is an opaque blob the connector controls; lobs-server stores it encrypted and passes it back on each call
- Connectors must never hard-code secrets; they read from `config.env`

**Auth strategies (pick one per connector):**

| Strategy | When to use |
|---|---|
| `oauth2_pkce` | User-facing services (Notion, Slack, Linear) |
| `api_key` | Simple API-key services (Linear, GitHub PAT) |
| `webhook_secret` | Inbound-only connectors (Discord bot, webhook receivers) |
| `service_account` | Google service accounts, JWT-based |

### 2. Sync

Pull data from the external service into lobs-server's data model.

```
sync(credentials: Credentials, since: datetime | None) -> SyncResult
```

- `since` is `None` on first sync (full pull), or a timestamp for incremental sync
- Returns a `SyncResult` with lists of normalized records: tasks, memories, calendar events, documents
- The orchestrator calls `sync()` on a schedule; connectors do not own the schedule

**SyncResult shape:**

```json
{
  "tasks": [...],
  "memories": [...],
  "calendar_events": [...],
  "documents": [...],
  "errors": [...],
  "cursor": "opaque-string-for-next-incremental-sync"
}
```

### 3. Webhook

Handle inbound push notifications from the external service.

```
verify_webhook(request: WebhookRequest) -> bool
handle_webhook(request: WebhookRequest, credentials: Credentials) -> SyncResult
```

- `verify_webhook` validates the signature (HMAC, token header, etc.) before any processing
- `handle_webhook` parses the payload and returns the same `SyncResult` shape as `sync()`
- lobs-server routes inbound POST requests to `POST /api/webhooks/{connector_slug}` and dispatches to the registered connector

### 4. Normalize

Convert a raw external record into a lobs-server record. Called internally by sync and webhook handlers.

```
normalize_item(raw: dict, record_type: RecordType) -> NormalizedRecord | None
```

- `record_type` is one of: `task`, `memory`, `calendar_event`, `document`
- Returns `None` if the raw item doesn't map to that type (skip it)
- Normalization is deterministic — same input always produces same output

**Key normalization rules:**

- `external_id` must be stable and unique (use the source system's ID)
- `external_source` is the connector slug (`discord`, `notion`, `linear`, etc.)
- `title` is required; `body` is optional
- Dates must be ISO 8601 with timezone

---

## Connector Manifest

Every connector ships a `connector.json` manifest. This is the source of truth for the registry.

```json
{
  "slug": "linear",
  "name": "Linear",
  "version": "1.0.0",
  "description": "Sync Linear issues as tasks. Supports incremental sync and webhooks.",
  "author": "lobs-team",
  "homepage": "https://linear.app",
  "icon": "linear.svg",

  "capabilities": ["auth", "sync", "webhook"],

  "auth": {
    "strategy": "oauth2_pkce",
    "scopes": ["read", "write"],
    "oauth_authorize_url": "https://linear.app/oauth/authorize",
    "oauth_token_url": "https://api.linear.app/oauth/token"
  },

  "sync": {
    "record_types": ["task"],
    "default_schedule_minutes": 15,
    "supports_incremental": true
  },

  "webhook": {
    "signature_header": "Linear-Signature",
    "signature_algorithm": "hmac-sha256"
  },

  "env": {
    "required": ["LINEAR_CLIENT_ID", "LINEAR_CLIENT_SECRET"],
    "optional": ["LINEAR_WEBHOOK_SECRET", "LINEAR_TEAM_ID"]
  },

  "settings": {
    "team_id": {
      "type": "string",
      "label": "Team ID",
      "description": "Restrict sync to this Linear team. Leave blank for all.",
      "required": false
    },
    "import_completed": {
      "type": "boolean",
      "label": "Import completed issues",
      "default": false,
      "required": false
    }
  }
}
```

**Required manifest fields:**

| Field | Description |
|---|---|
| `slug` | URL-safe unique identifier (`linear`, `discord`, `notion`) |
| `name` | Human-readable display name |
| `version` | SemVer |
| `capabilities` | Which of `auth`, `sync`, `webhook`, `normalize` this connector implements |
| `env.required` | Env vars the connector needs; server validates these at registration |

---

## Connector Registry

The registry is the in-process index of all loaded connectors. It lives at `app/connectors/registry.py`.

**Startup sequence:**

1. Server starts
2. Registry scans `connectors/` directory (and any `CONNECTORS_PATH` entries)
3. For each connector: load `connector.json`, import the Python package, validate the manifest
4. Register the connector instance under its slug
5. Any connector with missing required env vars is registered as `disabled` (won't sync or receive webhooks)

**Registry API (internal):**

```python
registry.get(slug: str) -> Connector | None
registry.list(capability: str = None) -> list[ConnectorMeta]
registry.is_enabled(slug: str) -> bool
```

**REST API (external, for Mission Control):**

```
GET  /api/connectors                    # List all registered connectors
GET  /api/connectors/{slug}             # Connector detail + status
POST /api/connectors/{slug}/sync        # Trigger manual sync
GET  /api/connectors/{slug}/sync/status # Last sync result
POST /api/connectors/{slug}/auth        # Begin OAuth flow
GET  /api/connectors/{slug}/auth/callback # OAuth callback
```

---

## Directory Layout

```
connectors/
├── registry.py           ← ConnectorRegistry class
├── base.py               ← BaseConnector abstract class + all shared types
├── types.py              ← SyncResult, NormalizedRecord, AuthResult, etc.
│
├── linear/
│   ├── connector.json    ← manifest
│   ├── connector.py      ← LinearConnector(BaseConnector)
│   └── README.md
│
├── discord/
│   ├── connector.json
│   ├── connector.py
│   └── README.md
│
├── notion/
│   ├── connector.json
│   ├── connector.py
│   └── README.md
│
├── slack/
│   ├── connector.json
│   ├── connector.py
│   └── README.md
│
└── _template/            ← Reference template for new connectors
    ├── connector.json
    ├── connector.py
    └── README.md
```

---

## Reference Connector Template

Copy `connectors/_template/` to get started.

### `_template/connector.json`

```json
{
  "slug": "myservice",
  "name": "My Service",
  "version": "0.1.0",
  "description": "One-line description of what this connector does.",
  "author": "your-name",

  "capabilities": ["auth", "sync"],

  "auth": {
    "strategy": "api_key"
  },

  "sync": {
    "record_types": ["task"],
    "default_schedule_minutes": 60,
    "supports_incremental": false
  },

  "env": {
    "required": ["MYSERVICE_API_KEY"],
    "optional": ["MYSERVICE_BASE_URL"]
  },

  "settings": {}
}
```

### `_template/connector.py`

```python
"""
MyService connector — replace with real implementation.

All public methods are called by lobs-server internals.
Do not rename them; do not add side effects outside the methods.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from connectors.base import BaseConnector
from connectors.types import (
    AuthResult,
    ConnectorConfig,
    Credentials,
    NormalizedRecord,
    RecordType,
    SyncResult,
    WebhookRequest,
)

if TYPE_CHECKING:
    pass  # type-only imports here


class MyServiceConnector(BaseConnector):
    """Connector for MyService."""

    # ── Auth ─────────────────────────────────────────────────────────────────

    async def authenticate(self, config: ConnectorConfig) -> AuthResult:
        """
        Validate the API key and return stored credentials.
        Called once during connector setup / credential refresh.
        """
        api_key = config.env["MYSERVICE_API_KEY"]

        # TODO: verify the key against the API
        # e.g., make a GET /me call and confirm 200

        return AuthResult(
            success=True,
            credentials=Credentials(data={"api_key": api_key}),
        )

    async def validate_credentials(self, credentials: Credentials) -> bool:
        """Return True if the stored credentials are still valid."""
        api_key = credentials.data.get("api_key")
        if not api_key:
            return False
        # TODO: lightweight check (e.g., HEAD request)
        return True

    # ── Sync ──────────────────────────────────────────────────────────────────

    async def sync(
        self,
        credentials: Credentials,
        since: datetime | None = None,
    ) -> SyncResult:
        """
        Pull records from MyService.

        Args:
            credentials: Stored credentials from authenticate().
            since: If set, fetch only records updated after this timestamp.
                   If None, do a full sync.

        Returns:
            SyncResult with normalized records and optional cursor.
        """
        api_key = credentials.data["api_key"]

        # TODO: fetch records from the API
        raw_items: list[dict] = []  # replace with real API call

        tasks = []
        errors = []

        for item in raw_items:
            try:
                record = self.normalize_item(item, RecordType.TASK)
                if record:
                    tasks.append(record)
            except Exception as exc:
                errors.append(str(exc))

        return SyncResult(tasks=tasks, errors=errors)

    # ── Normalize ─────────────────────────────────────────────────────────────

    def normalize_item(
        self,
        raw: dict,
        record_type: RecordType,
    ) -> NormalizedRecord | None:
        """
        Convert a raw API record to a lobs-server record.

        Return None to skip this item.
        """
        if record_type != RecordType.TASK:
            return None

        return NormalizedRecord(
            external_id=str(raw["id"]),
            external_source="myservice",
            record_type=RecordType.TASK,
            title=raw.get("title", "(untitled)"),
            body=raw.get("description"),
            status=self._map_status(raw.get("status")),
            created_at=raw.get("created_at"),
            updated_at=raw.get("updated_at"),
            url=raw.get("url"),
            metadata=raw,  # store full raw record for debugging
        )

    # ── Internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _map_status(external_status: str | None) -> str:
        """Map service-specific status strings to lobs task statuses."""
        mapping = {
            "open": "todo",
            "in_progress": "in_progress",
            "done": "done",
            "cancelled": "cancelled",
        }
        return mapping.get(external_status or "", "todo")
```

---

## Sample Connector: Discord (End-to-End)

Discord is inbound-only (no polling sync; messages arrive via webhook). This makes it a good, minimal example.

**What it does:**
- Receives Discord message events via webhook
- Normalizes `#task` channel messages into lobs tasks
- Normalizes `#memory` channel messages into lobs memories

### `connectors/discord/connector.json`

```json
{
  "slug": "discord",
  "name": "Discord",
  "version": "1.0.0",
  "description": "Ingest Discord messages as tasks or memories via webhook.",
  "author": "lobs-team",
  "homepage": "https://discord.com",

  "capabilities": ["webhook", "normalize"],

  "webhook": {
    "signature_header": "X-Signature-Ed25519",
    "signature_algorithm": "ed25519",
    "timestamp_header": "X-Signature-Timestamp"
  },

  "env": {
    "required": ["DISCORD_PUBLIC_KEY"],
    "optional": ["DISCORD_TASK_CHANNEL_ID", "DISCORD_MEMORY_CHANNEL_ID"]
  },

  "settings": {
    "task_channel_id": {
      "type": "string",
      "label": "Task channel ID",
      "description": "Messages in this channel become tasks.",
      "required": false
    },
    "memory_channel_id": {
      "type": "string",
      "label": "Memory channel ID",
      "description": "Messages in this channel become memories.",
      "required": false
    }
  }
}
```

### `connectors/discord/connector.py`

```python
"""
Discord connector — webhook-only.

Messages in configured channels are normalized to tasks or memories.
Discord webhooks use Ed25519 signatures; always verify before processing.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone

from connectors.base import BaseConnector
from connectors.types import (
    ConnectorConfig,
    Credentials,
    NormalizedRecord,
    RecordType,
    SyncResult,
    WebhookRequest,
)


class DiscordConnector(BaseConnector):
    """Inbound Discord webhook connector."""

    # ── Webhook ───────────────────────────────────────────────────────────────

    async def verify_webhook(self, request: WebhookRequest) -> bool:
        """
        Verify the Ed25519 signature Discord sends with every webhook.
        See: https://discord.com/developers/docs/interactions/overview#security-and-authorization
        """
        public_key = request.config.env.get("DISCORD_PUBLIC_KEY", "")
        timestamp = request.headers.get("X-Signature-Timestamp", "")
        signature = request.headers.get("X-Signature-Ed25519", "")

        if not all([public_key, timestamp, signature]):
            return False

        try:
            from nacl.signing import VerifyKey
            from nacl.exceptions import BadSignatureError

            verify_key = VerifyKey(bytes.fromhex(public_key))
            message = (timestamp + request.raw_body).encode()
            verify_key.verify(message, bytes.fromhex(signature))
            return True
        except Exception:
            return False

    async def handle_webhook(
        self,
        request: WebhookRequest,
        credentials: Credentials,
    ) -> SyncResult:
        """Parse Discord message event and normalize to tasks/memories."""
        payload = request.json_body
        event_type = payload.get("t")

        # Discord sends a PING on first connection — respond but don't process
        if payload.get("type") == 1:
            return SyncResult()  # empty result; server returns {"type": 1}

        if event_type != "MESSAGE_CREATE":
            return SyncResult()  # we only handle message events

        message = payload.get("d", {})
        channel_id = message.get("channel_id", "")
        task_channel = request.config.env.get("DISCORD_TASK_CHANNEL_ID", "")
        memory_channel = request.config.env.get("DISCORD_MEMORY_CHANNEL_ID", "")

        tasks = []
        memories = []

        if channel_id == task_channel:
            record = self.normalize_item(message, RecordType.TASK)
            if record:
                tasks.append(record)

        elif channel_id == memory_channel:
            record = self.normalize_item(message, RecordType.MEMORY)
            if record:
                memories.append(record)

        return SyncResult(tasks=tasks, memories=memories)

    # ── Normalize ─────────────────────────────────────────────────────────────

    def normalize_item(
        self,
        raw: dict,
        record_type: RecordType,
    ) -> NormalizedRecord | None:
        """Map a Discord message to a task or memory."""
        content = raw.get("content", "").strip()
        if not content:
            return None

        author = raw.get("author", {})
        author_name = author.get("username", "unknown")
        message_id = raw.get("id", "")
        guild_id = raw.get("guild_id", "")
        channel_id = raw.get("channel_id", "")
        timestamp_str = raw.get("timestamp")

        # Build a stable external_id
        external_id = f"discord-{message_id}"

        # Build a URL back to the message (if we have guild + channel + message)
        url = None
        if guild_id and channel_id and message_id:
            url = f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"

        if record_type == RecordType.TASK:
            # First line is the title; rest is body
            lines = content.splitlines()
            title = lines[0][:200]  # truncate to sane length
            body = "\n".join(lines[1:]).strip() or None

            return NormalizedRecord(
                external_id=external_id,
                external_source="discord",
                record_type=RecordType.TASK,
                title=title,
                body=body,
                status="todo",
                created_at=timestamp_str,
                updated_at=timestamp_str,
                url=url,
                metadata={
                    "discord_message_id": message_id,
                    "discord_author": author_name,
                    "discord_channel_id": channel_id,
                },
            )

        elif record_type == RecordType.MEMORY:
            return NormalizedRecord(
                external_id=external_id,
                external_source="discord",
                record_type=RecordType.MEMORY,
                title=f"Discord: {author_name}",
                body=content,
                created_at=timestamp_str,
                updated_at=timestamp_str,
                url=url,
                metadata={
                    "discord_message_id": message_id,
                    "discord_author": author_name,
                },
            )

        return None
```

### Discord: Setup walkthrough

1. **Set env vars:**
   ```bash
   DISCORD_PUBLIC_KEY=<from Discord Developer Portal>
   DISCORD_TASK_CHANNEL_ID=<channel ID where #task messages go>
   DISCORD_MEMORY_CHANNEL_ID=<channel ID where #memory messages go>
   ```

2. **Register the webhook URL in Discord:**
   Go to Developer Portal → Bot → Interactions Endpoint URL:
   ```
   https://your-lobs-server.example.com/api/webhooks/discord
   ```

3. **Discord pings the endpoint on save** — lobs-server routes it to `DiscordConnector.verify_webhook()` and responds with `{"type": 1}`.

4. **Send a test message in your task channel.** lobs-server creates a task automatically.

---

## Shared Types Reference

These types live in `connectors/types.py` and are imported by every connector.

```python
@dataclass
class ConnectorConfig:
    slug: str
    env: dict[str, str]          # env vars from manifest.env.required/optional
    settings: dict[str, Any]     # user-configured settings from manifest.settings

@dataclass
class Credentials:
    data: dict[str, Any]         # connector-controlled blob; stored encrypted

@dataclass
class AuthResult:
    success: bool
    credentials: Credentials | None
    error: str | None = None

@dataclass
class NormalizedRecord:
    external_id: str             # stable unique ID from the source system
    external_source: str         # connector slug
    record_type: RecordType      # TASK, MEMORY, CALENDAR_EVENT, DOCUMENT
    title: str
    body: str | None = None
    status: str = "todo"         # for tasks
    created_at: str | None = None   # ISO 8601
    updated_at: str | None = None   # ISO 8601
    url: str | None = None
    metadata: dict = field(default_factory=dict)

@dataclass
class SyncResult:
    tasks: list[NormalizedRecord] = field(default_factory=list)
    memories: list[NormalizedRecord] = field(default_factory=list)
    calendar_events: list[NormalizedRecord] = field(default_factory=list)
    documents: list[NormalizedRecord] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    cursor: str | None = None    # opaque; passed as `since` context on next sync

@dataclass
class WebhookRequest:
    config: ConnectorConfig
    headers: dict[str, str]
    raw_body: str
    json_body: dict

class RecordType(str, Enum):
    TASK = "task"
    MEMORY = "memory"
    CALENDAR_EVENT = "calendar_event"
    DOCUMENT = "document"
```

---

## BaseConnector

All connectors extend `BaseConnector` from `connectors/base.py`.

```python
class BaseConnector(ABC):
    """Abstract base class for all lobs-server connectors."""

    # Override if your connector supports this capability.
    # Default implementations raise NotImplementedError.

    async def authenticate(self, config: ConnectorConfig) -> AuthResult:
        raise NotImplementedError

    async def refresh_token(self, credentials: Credentials) -> Credentials:
        raise NotImplementedError

    async def validate_credentials(self, credentials: Credentials) -> bool:
        raise NotImplementedError

    async def sync(self, credentials: Credentials, since: datetime | None) -> SyncResult:
        raise NotImplementedError

    async def verify_webhook(self, request: WebhookRequest) -> bool:
        raise NotImplementedError

    async def handle_webhook(self, request: WebhookRequest, credentials: Credentials) -> SyncResult:
        raise NotImplementedError

    def normalize_item(self, raw: dict, record_type: RecordType) -> NormalizedRecord | None:
        raise NotImplementedError
```

Connectors only implement what they declare in `capabilities`. Calling an unimplemented method raises `NotImplementedError` — the registry checks capabilities before calling.

---

## Routing: Webhooks

lobs-server exposes a single webhook endpoint. Connectors don't register their own routes.

```
POST /api/webhooks/{slug}
```

The router:
1. Looks up the connector by `slug`
2. Calls `verify_webhook()` — returns 401 if it fails
3. Calls `handle_webhook()` — returns the SyncResult
4. Persists normalized records (upsert on `external_id + external_source`)

Webhook responses follow the connector's needs. Discord requires `{"type": 1}` for PING; the registry handles this transparently.

---

## Idempotency

All sync and webhook results are upserted, not inserted. The key is:

```
(external_id, external_source)
```

If a record already exists with the same key, it is updated. Duplicate webhook deliveries are safe.

---

## Adding a New Connector — Checklist

1. Copy `connectors/_template/` to `connectors/your-slug/`
2. Fill in `connector.json`:
   - Set `slug`, `name`, `version`, `capabilities`
   - List all required and optional env vars
3. Implement `connector.py`:
   - Extend `BaseConnector`
   - Implement only the methods listed in `capabilities`
   - Return `NormalizedRecord` objects from normalize
4. Add a `README.md` with:
   - What this connector does
   - Setup steps (env vars, OAuth flow, webhook URL)
   - One example of input → normalized output
5. Register for local testing:
   ```bash
   CONNECTORS_PATH=connectors/your-slug ./bin/run
   ```
6. Test the webhook (if applicable):
   ```bash
   curl -X POST http://localhost:8000/api/webhooks/your-slug \
     -H "Content-Type: application/json" \
     -d '{"test": true}'
   ```

---

## Connector Roadmap

| Connector | Capabilities | Priority | Notes |
|---|---|---|---|
| Discord | webhook, normalize | **MVP** | Inbound messages → tasks, memories |
| Linear | auth, sync, webhook | High | Issues → tasks; two-way status sync |
| Notion | auth, sync | High | Pages → documents; databases → tasks |
| Slack | auth, webhook, normalize | Medium | Channel messages → memories; slash commands |
| GitHub _(migrate)_ | auth, sync | Low | Refactor existing `github_sync.py` to SDK |
| Google Calendar _(migrate)_ | auth, sync | Low | Refactor existing `google_calendar.py` to SDK |

---

## Design Decisions

**Why not reuse the agent plugin system?**

The plugin system (see `docs/plugin-system-ADR.md`) handles agent skill extensions — custom tools agents can call during task execution. Connectors are different: they pull data from external services into lobs-server's data model. Different purpose, different interface, different lifecycle.

**Why Python packages instead of subprocess scripts?**

Connectors run in-process, not as subprocesses. They need async I/O (HTTP calls) and access to the database session for upserts. Subprocess overhead would hurt webhook response times. Security isolation is handled by the permission model (env var allowlist in the manifest) rather than sandboxing.

**Why a single `/api/webhooks/{slug}` endpoint?**

Fewer moving parts. No per-connector route registration. Mission Control can display all configured webhooks in one place. Each connector handles its own signature verification.

**Why store raw metadata?**

The `metadata` field on `NormalizedRecord` stores the full raw item. This lets you debug normalization bugs, re-normalize after schema changes, and build connector-specific features without schema migrations.

---

## Implementation Handoffs

Three files need to be created for the programmer agent:

| File | What it does |
|---|---|
| `connectors/types.py` | All shared dataclasses and enums |
| `connectors/base.py` | BaseConnector abstract class |
| `connectors/registry.py` | ConnectorRegistry: scan, load, validate |
| `app/routers/connectors.py` | REST API (list, sync, status, auth, callback) |
| `app/routers/webhooks.py` | POST /api/webhooks/{slug} dispatcher |

The Discord connector in `connectors/discord/` is the reference implementation to build and test against. It has no sync (webhook-only), no auth (public key only), so it's the smallest end-to-end path.

---

*See also: [plugin-system-ADR.md](plugin-system-ADR.md) (agent skills), [plugin-system-implementation-guide.md](plugin-system-implementation-guide.md)*
