"""Tests for webhook API endpoints."""

import hashlib
import hmac
import json
from datetime import datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models import WebhookRegistration, WebhookEvent, WebhookDelivery, Task


@pytest.mark.asyncio
async def test_create_webhook_registration(client: AsyncClient):
    """Test creating a webhook registration."""
    webhook_data = {
        "name": "GitHub Issues",
        "provider": "github",
        "secret": "test-secret-123",
        "event_filters": ["issues"],
        "target_action": "create_task",
        "action_config": {"project_id": "inbox"},
        "active": True
    }
    
    response = await client.post("/api/webhooks", json=webhook_data)
    assert response.status_code == 200
    
    data = response.json()
    assert data["name"] == "GitHub Issues"
    assert data["provider"] == "github"
    assert data["secret"] == "test-secret-123"
    assert data["event_filters"] == ["issues"]
    assert data["target_action"] == "create_task"
    assert data["active"] is True
    assert "id" in data
    assert "created_at" in data


@pytest.mark.asyncio
async def test_list_webhooks(client: AsyncClient, db_session):
    """Test listing webhook registrations."""
    # Create test webhooks
    webhook1 = WebhookRegistration(
        id="webhook-1",
        name="GitHub",
        provider="github",
        secret="secret1",
        event_filters=["issues"],
        target_action="create_task",
        action_config={},
        active=True
    )
    webhook2 = WebhookRegistration(
        id="webhook-2",
        name="Slack",
        provider="slack",
        secret="secret2",
        event_filters=["message"],
        target_action="trigger_agent",
        action_config={},
        active=False
    )
    
    db_session.add_all([webhook1, webhook2])
    await db_session.commit()
    
    # List all
    response = await client.get("/api/webhooks")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    
    # Filter by provider
    response = await client.get("/api/webhooks?provider=github")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["provider"] == "github"
    
    # Filter by active
    response = await client.get("/api/webhooks?active=true")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["active"] is True


@pytest.mark.asyncio
async def test_get_webhook(client: AsyncClient, db_session):
    """Test getting a webhook by ID."""
    webhook = WebhookRegistration(
        id="webhook-get",
        name="Test Webhook",
        provider="custom",
        secret="secret",
        event_filters=[],
        target_action="create_task",
        action_config={},
        active=True
    )
    db_session.add(webhook)
    await db_session.commit()
    
    response = await client.get("/api/webhooks/webhook-get")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "webhook-get"
    assert data["name"] == "Test Webhook"


@pytest.mark.asyncio
async def test_update_webhook(client: AsyncClient, db_session):
    """Test updating a webhook."""
    webhook = WebhookRegistration(
        id="webhook-update",
        name="Original Name",
        provider="github",
        secret="original-secret",
        event_filters=["issues"],
        target_action="create_task",
        action_config={},
        active=True
    )
    db_session.add(webhook)
    await db_session.commit()
    
    # Update
    update_data = {
        "name": "Updated Name",
        "active": False
    }
    response = await client.put("/api/webhooks/webhook-update", json=update_data)
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Updated Name"
    assert data["active"] is False
    assert data["secret"] == "original-secret"  # Unchanged


@pytest.mark.asyncio
async def test_delete_webhook(client: AsyncClient, db_session):
    """Test deleting a webhook."""
    webhook = WebhookRegistration(
        id="webhook-delete",
        name="To Delete",
        provider="github",
        secret="secret",
        event_filters=[],
        target_action="create_task",
        action_config={},
        active=True
    )
    db_session.add(webhook)
    await db_session.commit()
    
    response = await client.delete("/api/webhooks/webhook-delete")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "deleted"
    
    # Verify deletion
    result = await db_session.execute(
        select(WebhookRegistration).where(WebhookRegistration.id == "webhook-delete")
    )
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_receive_github_webhook_valid_signature(client: AsyncClient, db_session, sample_project):
    """Test receiving a GitHub webhook with valid signature."""
    # Create webhook registration
    secret = "github-webhook-secret"
    webhook = WebhookRegistration(
        id="github-webhook",
        name="GitHub Issues",
        provider="github",
        secret=secret,
        event_filters=["issues"],
        target_action="create_task",
        action_config={"project_id": sample_project["id"]},
        active=True
    )
    db_session.add(webhook)
    await db_session.commit()
    
    # Create GitHub webhook payload
    payload = {
        "action": "opened",
        "issue": {
            "number": 123,
            "title": "Test Issue",
            "body": "Issue description"
        }
    }
    
    body = json.dumps(payload).encode('utf-8')
    
    # Generate valid signature
    signature = hmac.new(
        secret.encode('utf-8'),
        body,
        hashlib.sha256
    ).hexdigest()
    
    # Send webhook
    response = await client.post(
        "/api/webhooks/receive/github",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "issues",
            "X-Hub-Signature-256": f"sha256={signature}"
        }
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "processed"
    assert "event_id" in data
    
    # Verify event was created
    result = await db_session.execute(
        select(WebhookEvent).where(WebhookEvent.provider == "github")
    )
    event = result.scalar_one_or_none()
    assert event is not None
    assert event.event_type == "issues"
    assert event.signature_valid is True
    assert event.status == "processed"
    
    # Verify task was created
    result = await db_session.execute(
        select(Task).where(Task.external_source == "github")
    )
    task = result.scalar_one_or_none()
    assert task is not None
    assert "GitHub #123" in task.title
    assert "Test Issue" in task.title
    assert task.github_issue_number == 123


@pytest.mark.asyncio
async def test_receive_github_webhook_invalid_signature(client: AsyncClient, db_session):
    """Test receiving a GitHub webhook with invalid signature."""
    webhook = WebhookRegistration(
        id="github-webhook-invalid",
        name="GitHub Issues",
        provider="github",
        secret="correct-secret",
        event_filters=["issues"],
        target_action="create_task",
        action_config={},
        active=True
    )
    db_session.add(webhook)
    await db_session.commit()
    
    payload = {"action": "opened", "issue": {"number": 456}}
    body = json.dumps(payload).encode('utf-8')
    
    # Wrong signature
    wrong_signature = "sha256=wrong_signature_hash"
    
    response = await client.post(
        "/api/webhooks/receive/github",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "issues",
            "X-Hub-Signature-256": wrong_signature
        }
    )
    
    assert response.status_code == 403
    assert "Invalid signature" in response.json()["detail"]


@pytest.mark.asyncio
async def test_receive_slack_webhook_valid_signature(client: AsyncClient, db_session):
    """Test receiving a Slack webhook with valid signature."""
    secret = "slack-signing-secret"
    webhook = WebhookRegistration(
        id="slack-webhook",
        name="Slack Events",
        provider="slack",
        secret=secret,
        event_filters=["message"],
        target_action="trigger_agent",
        action_config={},
        active=True
    )
    db_session.add(webhook)
    await db_session.commit()
    
    payload = {
        "type": "message",
        "text": "Hello from Slack"
    }
    body = json.dumps(payload).encode('utf-8')
    
    # Generate Slack signature
    timestamp = str(int(datetime.utcnow().timestamp()))
    sig_basestring = f"v0:{timestamp}:{body.decode('utf-8')}"
    signature = 'v0=' + hmac.new(
        secret.encode('utf-8'),
        sig_basestring.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    
    response = await client.post(
        "/api/webhooks/receive/slack",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Slack-Signature": signature,
            "X-Slack-Request-Timestamp": timestamp
        }
    )
    
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_receive_slack_webhook_replay_attack(client: AsyncClient, db_session):
    """Test that old Slack webhook timestamps are rejected."""
    secret = "slack-signing-secret"
    webhook = WebhookRegistration(
        id="slack-webhook-replay",
        name="Slack Events",
        provider="slack",
        secret=secret,
        event_filters=[],
        target_action="trigger_agent",
        action_config={},
        active=True
    )
    db_session.add(webhook)
    await db_session.commit()
    
    payload = {"type": "message"}
    body = json.dumps(payload).encode('utf-8')
    
    # Old timestamp (>5 minutes ago)
    old_timestamp = str(int((datetime.utcnow() - timedelta(minutes=10)).timestamp()))
    sig_basestring = f"v0:{old_timestamp}:{body.decode('utf-8')}"
    signature = 'v0=' + hmac.new(
        secret.encode('utf-8'),
        sig_basestring.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    
    response = await client.post(
        "/api/webhooks/receive/slack",
        content=body,
        headers={
            "X-Slack-Signature": signature,
            "X-Slack-Request-Timestamp": old_timestamp
        }
    )
    
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_receive_custom_webhook(client: AsyncClient, db_session, sample_project):
    """Test receiving a custom webhook with secret in payload."""
    secret = "custom-webhook-secret"
    webhook = WebhookRegistration(
        id="custom-webhook",
        name="Custom Integration",
        provider="custom",
        secret=secret,
        event_filters=[],
        target_action="create_task",
        action_config={"project_id": sample_project["id"]},
        active=True
    )
    db_session.add(webhook)
    await db_session.commit()
    
    payload = {
        "secret": secret,
        "title": "Custom Task",
        "description": "Task from custom webhook"
    }
    
    response = await client.post(
        "/api/webhooks/receive/custom",
        json=payload
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "processed"
    
    # Verify task created
    result = await db_session.execute(
        select(Task).where(Task.external_source == "custom")
    )
    task = result.scalar_one_or_none()
    assert task is not None
    assert task.title == "Custom Task"


@pytest.mark.asyncio
async def test_receive_webhook_no_matching_registration(client: AsyncClient):
    """Test receiving webhook with no matching registration."""
    payload = {"data": "test"}
    
    response = await client.post(
        "/api/webhooks/receive/nonexistent",
        json=payload
    )
    
    assert response.status_code == 404
    assert "No webhook configured" in response.json()["detail"]


@pytest.mark.asyncio
async def test_receive_webhook_event_filter(client: AsyncClient, db_session):
    """Test that webhooks only process events matching their filters."""
    webhook = WebhookRegistration(
        id="filtered-webhook",
        name="Filtered",
        provider="github",
        secret="secret",
        event_filters=["pull_request"],  # Only PRs, not issues
        target_action="create_task",
        action_config={},
        active=True
    )
    db_session.add(webhook)
    await db_session.commit()
    
    payload = {"action": "opened", "issue": {"number": 1}}
    body = json.dumps(payload).encode('utf-8')
    signature = hmac.new(
        "secret".encode('utf-8'),
        body,
        hashlib.sha256
    ).hexdigest()
    
    # Send issues event (should be rejected by filter)
    response = await client.post(
        "/api/webhooks/receive/github",
        content=body,
        headers={
            "X-GitHub-Event": "issues",
            "X-Hub-Signature-256": f"sha256={signature}"
        }
    )
    
    assert response.status_code == 403
    assert "Invalid signature or event not configured" in response.json()["detail"]


@pytest.mark.asyncio
async def test_list_webhook_events(client: AsyncClient, db_session):
    """Test listing webhook events."""
    # Create test events
    event1 = WebhookEvent(
        id="event-1",
        provider="github",
        event_type="issues",
        payload={"test": "data"},
        signature_valid=True,
        status="processed"
    )
    event2 = WebhookEvent(
        id="event-2",
        provider="slack",
        event_type="message",
        payload={"test": "data"},
        signature_valid=True,
        status="pending"
    )
    
    db_session.add_all([event1, event2])
    await db_session.commit()
    
    # List all
    response = await client.get("/api/webhooks/events")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    
    # Filter by provider
    response = await client.get("/api/webhooks/events?provider=github")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["provider"] == "github"
    
    # Filter by status
    response = await client.get("/api/webhooks/events?status=pending")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_webhook_delivery_on_success(client: AsyncClient, db_session, sample_project):
    """Test that successful webhook processing creates a delivery record."""
    webhook = WebhookRegistration(
        id="delivery-test",
        name="Test",
        provider="custom",
        secret="secret",
        event_filters=[],
        target_action="create_task",
        action_config={"project_id": sample_project["id"]},
        active=True
    )
    db_session.add(webhook)
    await db_session.commit()
    
    payload = {
        "secret": "secret",
        "title": "Test Task"
    }
    
    response = await client.post("/api/webhooks/receive/custom", json=payload)
    assert response.status_code == 200
    
    # Check delivery record
    result = await db_session.execute(
        select(WebhookDelivery)
    )
    delivery = result.scalar_one_or_none()
    assert delivery is not None
    assert delivery.status == "success"
    assert delivery.attempt == 1


@pytest.mark.asyncio
async def test_webhook_inactive(client: AsyncClient, db_session):
    """Test that inactive webhooks don't process events."""
    webhook = WebhookRegistration(
        id="inactive-webhook",
        name="Inactive",
        provider="custom",
        secret="secret",
        event_filters=[],
        target_action="create_task",
        action_config={},
        active=False  # Inactive
    )
    db_session.add(webhook)
    await db_session.commit()
    
    payload = {"secret": "secret", "title": "Test"}
    
    response = await client.post("/api/webhooks/receive/custom", json=payload)
    assert response.status_code == 404
