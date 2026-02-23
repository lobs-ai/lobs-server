"""Webhook management and receiver endpoints."""

import hashlib
import hmac
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Header, Request
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth
from app.database import get_db
from app.models import WebhookRegistration as WebhookRegistrationModel
from app.models import WebhookEvent as WebhookEventModel
from app.models import WebhookDelivery as WebhookDeliveryModel
from app.models import Task as TaskModel
from app.models import Project as ProjectModel
from app.schemas import (
    WebhookRegistration,
    WebhookRegistrationCreate,
    WebhookRegistrationUpdate,
    WebhookEvent,
)
from app.utils.sanitize import (
    sanitize_webhook_payload,
    sanitize_github_issue,
    sanitize_html,
    validate_json_depth,
)
from app.config import settings

logger = logging.getLogger(__name__)
security_logger = logging.getLogger("app.security")
router = APIRouter(tags=["webhooks"])


# Webhook Registration Management

@router.post("/webhooks", response_model=WebhookRegistration, dependencies=[Depends(require_auth)])
async def create_webhook(
    webhook: WebhookRegistrationCreate,
    db: AsyncSession = Depends(get_db)
):
    """Register a new webhook endpoint."""
    webhook_id = str(uuid4())
    
    db_webhook = WebhookRegistrationModel(
        id=webhook_id,
        name=webhook.name,
        provider=webhook.provider,
        secret=webhook.secret,
        event_filters=webhook.event_filters or [],
        target_action=webhook.target_action,
        action_config=webhook.action_config or {},
        active=webhook.active,
    )
    
    db.add(db_webhook)
    await db.commit()
    await db.refresh(db_webhook)
    
    logger.info(f"Created webhook registration: {webhook_id} ({webhook.provider})")
    return db_webhook


@router.get("/webhooks", response_model=list[WebhookRegistration], dependencies=[Depends(require_auth)])
async def list_webhooks(
    provider: Optional[str] = None,
    active: Optional[bool] = None,
    db: AsyncSession = Depends(get_db)
):
    """List all registered webhooks with optional filtering."""
    query = select(WebhookRegistrationModel)
    
    if provider:
        query = query.where(WebhookRegistrationModel.provider == provider)
    if active is not None:
        query = query.where(WebhookRegistrationModel.active == active)
    
    query = query.order_by(desc(WebhookRegistrationModel.created_at))
    
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/webhooks/{webhook_id}", response_model=WebhookRegistration, dependencies=[Depends(require_auth)])
async def get_webhook(
    webhook_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Get webhook details by ID."""
    result = await db.execute(
        select(WebhookRegistrationModel).where(WebhookRegistrationModel.id == webhook_id)
    )
    webhook = result.scalar_one_or_none()
    
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook not found")
    
    return webhook


@router.put("/webhooks/{webhook_id}", response_model=WebhookRegistration, dependencies=[Depends(require_auth)])
async def update_webhook(
    webhook_id: str,
    updates: WebhookRegistrationUpdate,
    db: AsyncSession = Depends(get_db)
):
    """Update webhook configuration."""
    result = await db.execute(
        select(WebhookRegistrationModel).where(WebhookRegistrationModel.id == webhook_id)
    )
    webhook = result.scalar_one_or_none()
    
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook not found")
    
    # Update fields
    if updates.name is not None:
        webhook.name = updates.name
    if updates.secret is not None:
        webhook.secret = updates.secret
    if updates.event_filters is not None:
        webhook.event_filters = updates.event_filters
    if updates.target_action is not None:
        webhook.target_action = updates.target_action
    if updates.action_config is not None:
        webhook.action_config = updates.action_config
    if updates.active is not None:
        webhook.active = updates.active
    
    webhook.updated_at = datetime.now(timezone.utc)
    
    await db.commit()
    await db.refresh(webhook)
    
    logger.info(f"Updated webhook: {webhook_id}")
    return webhook


@router.delete("/webhooks/{webhook_id}", dependencies=[Depends(require_auth)])
async def delete_webhook(
    webhook_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Delete a webhook registration."""
    result = await db.execute(
        select(WebhookRegistrationModel).where(WebhookRegistrationModel.id == webhook_id)
    )
    webhook = result.scalar_one_or_none()
    
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook not found")
    
    await db.delete(webhook)
    await db.commit()
    
    logger.info(f"Deleted webhook: {webhook_id}")
    return {"status": "deleted", "id": webhook_id}


# Webhook Event Receiver

@router.post("/webhooks/receive/{provider}")
async def receive_webhook(
    provider: str,
    request: Request,
    x_hub_signature_256: Optional[str] = Header(None),
    x_github_event: Optional[str] = Header(None),
    x_slack_signature: Optional[str] = Header(None),
    x_slack_request_timestamp: Optional[str] = Header(None),
    x_webhook_signature: Optional[str] = Header(None),  # For custom webhooks with HMAC
    db: AsyncSession = Depends(get_db)
):
    """
    Receive webhook events from external providers.
    
    Supports:
    - GitHub (signature verification via X-Hub-Signature-256)
    - Slack (signature verification via X-Slack-Signature)
    - Custom webhooks (HMAC via X-Webhook-Signature, or deprecated payload secret)
    
    Security controls:
    - Signature verification (HMAC-SHA256)
    - Payload size limiting (via middleware)
    - JSON depth validation
    - HTML sanitization
    - Generic error messages
    """
    client_ip = request.client.host if request.client else "unknown"
    
    # Read raw body for signature verification
    try:
        body = await request.body()
    except Exception as e:
        security_logger.error(f"Failed to read webhook body from {client_ip}: {e}")
        raise HTTPException(status_code=400, detail="Invalid request")
    
    # Parse JSON payload
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as e:
        security_logger.warning(
            f"Invalid JSON from {provider} webhook at {client_ip}: {e}"
        )
        raise HTTPException(status_code=400, detail="Invalid request format")
    
    # Validate JSON nesting depth (prevent stack overflow attacks)
    if not validate_json_depth(payload):
        security_logger.warning(
            f"JSON depth limit exceeded from {provider} webhook at {client_ip}"
        )
        raise HTTPException(status_code=400, detail="Invalid request format")
    
    # Determine event type
    event_type = "unknown"
    if provider == "github":
        event_type = x_github_event or "unknown"
    elif provider == "slack":
        event_type = payload.get("type", "unknown")
    else:
        event_type = payload.get("event_type", "unknown")
    
    # Find matching webhook registration
    result = await db.execute(
        select(WebhookRegistrationModel).where(
            WebhookRegistrationModel.provider == provider,
            WebhookRegistrationModel.active == True
        )
    )
    webhooks = result.scalars().all()
    
    if not webhooks:
        security_logger.warning(
            f"No active webhook for provider '{provider}' from {client_ip}"
        )
        # Generic error - don't reveal if provider is configured
        raise HTTPException(status_code=403, detail="Unauthorized")
    
    # Try each webhook until we find one that accepts this event
    matched_webhook = None
    signature_valid = False
    
    for webhook in webhooks:
        # Check event filter
        if webhook.event_filters and event_type not in webhook.event_filters:
            continue
        
        # Verify signature
        if provider == "github":
            signature_valid = verify_github_signature(body, webhook.secret, x_hub_signature_256)
        elif provider == "slack":
            signature_valid = verify_slack_signature(body, webhook.secret, x_slack_signature, x_slack_request_timestamp)
        else:
            # For custom webhooks: try HMAC first (preferred), fall back to payload secret (deprecated)
            signature_valid = verify_custom_signature(body, webhook.secret, x_webhook_signature)
            
            # Backward compatibility: check payload secret (deprecated)
            if not signature_valid and payload.get("secret") == webhook.secret:
                signature_valid = True
                security_logger.warning(
                    f"Webhook {webhook.id} using deprecated payload-based authentication. "
                    f"Please migrate to HMAC signatures (X-Webhook-Signature header)."
                )
        
        if signature_valid:
            matched_webhook = webhook
            break
    
    if not matched_webhook:
        security_logger.warning(
            f"Signature validation failed for {provider} event {event_type} from {client_ip}"
        )
        # Generic error - same as missing webhook to prevent enumeration
        raise HTTPException(status_code=403, detail="Unauthorized")
    
    # Create webhook event record
    event_id = str(uuid4())
    headers_dict = dict(request.headers)
    
    db_event = WebhookEventModel(
        id=event_id,
        registration_id=matched_webhook.id,
        provider=provider,
        event_type=event_type,
        payload=payload,
        headers=headers_dict,
        signature_valid=signature_valid,
        status="pending",
    )
    
    db.add(db_event)
    
    # Update last received timestamp on registration
    matched_webhook.last_received_at = datetime.now(timezone.utc)
    
    await db.commit()
    
    # Process event asynchronously (dispatch to handler)
    try:
        result = await process_webhook_event(db, matched_webhook, db_event, payload)
        
        # Update event status
        db_event.status = "processed"
        db_event.processing_result = result
        db_event.processed_at = datetime.now(timezone.utc)
        
        # Record successful delivery
        delivery = WebhookDeliveryModel(
            id=str(uuid4()),
            event_id=event_id,
            attempt=1,
            status="success",
        )
        db.add(delivery)
        
        await db.commit()
        
        logger.info(f"Processed webhook event: {event_id} ({provider}/{event_type})")
        return {"status": "processed", "event_id": event_id, "result": result}
        
    except Exception as e:
        logger.error(f"Failed to process webhook event {event_id}: {e}")
        
        # Update event status
        db_event.status = "failed"
        db_event.processing_result = {"error": str(e)}
        db_event.processed_at = datetime.now(timezone.utc)
        
        # Record failed delivery (will be retried)
        delivery = WebhookDeliveryModel(
            id=str(uuid4()),
            event_id=event_id,
            attempt=1,
            status="failed",
            error_message=str(e),
            next_retry_at=datetime.now(timezone.utc) + timedelta(minutes=5),  # Retry in 5 mins
        )
        db.add(delivery)
        
        await db.commit()
        
        return {"status": "failed", "event_id": event_id, "error": str(e)}


@router.get("/webhooks/events", response_model=list[WebhookEvent], dependencies=[Depends(require_auth)])
async def list_webhook_events(
    provider: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db)
):
    """List webhook events with optional filtering."""
    query = select(WebhookEventModel)
    
    if provider:
        query = query.where(WebhookEventModel.provider == provider)
    if status:
        query = query.where(WebhookEventModel.status == status)
    
    query = query.order_by(desc(WebhookEventModel.created_at)).limit(limit)
    
    result = await db.execute(query)
    return result.scalars().all()


# Signature Verification Functions

def verify_github_signature(body: bytes, secret: str, signature_header: Optional[str]) -> bool:
    """Verify GitHub webhook signature using HMAC SHA256."""
    if not signature_header:
        return False
    
    try:
        # GitHub sends signature as "sha256=<hash>"
        expected_sig = hmac.new(
            secret.encode('utf-8'),
            body,
            hashlib.sha256
        ).hexdigest()
        
        received_sig = signature_header.replace('sha256=', '')
        
        return hmac.compare_digest(expected_sig, received_sig)
    except Exception as e:
        logger.error(f"GitHub signature verification failed: {e}")
        return False


def verify_slack_signature(
    body: bytes,
    secret: str,
    signature_header: Optional[str],
    timestamp_header: Optional[str]
) -> bool:
    """Verify Slack webhook signature."""
    if not signature_header or not timestamp_header:
        return False
    
    try:
        # Prevent replay attacks - reject if timestamp is > 5 minutes old
        request_timestamp = int(timestamp_header)
        current_timestamp = int(datetime.now(timezone.utc).timestamp())
        
        if abs(current_timestamp - request_timestamp) > 300:
            logger.warning("Slack webhook timestamp too old")
            return False
        
        # Slack signature verification
        sig_basestring = f"v0:{timestamp_header}:{body.decode('utf-8')}"
        expected_sig = 'v0=' + hmac.new(
            secret.encode('utf-8'),
            sig_basestring.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        return hmac.compare_digest(expected_sig, signature_header)
    except Exception as e:
        logger.error(f"Slack signature verification failed: {e}")
        return False


# Event Processing

async def process_webhook_event(
    db: AsyncSession,
    webhook: WebhookRegistrationModel,
    event: WebhookEventModel,
    payload: dict
) -> dict:
    """
    Process webhook event based on target action.
    
    Supported actions:
    - create_task: Create a new task from webhook data
    - trigger_agent: Trigger an agent with webhook data
    - update_project: Update project metadata
    - custom: Execute custom action defined in action_config
    """
    action = webhook.target_action
    config = webhook.action_config or {}
    
    if action == "create_task":
        return await create_task_from_webhook(db, webhook, payload, config)
    elif action == "trigger_agent":
        return await trigger_agent_from_webhook(db, webhook, payload, config)
    elif action == "update_project":
        return await update_project_from_webhook(db, webhook, payload, config)
    else:
        logger.warning(f"Unsupported action: {action}")
        return {"status": "ignored", "reason": f"Unsupported action: {action}"}


async def create_task_from_webhook(
    db: AsyncSession,
    webhook: WebhookRegistrationModel,
    payload: dict,
    config: dict
) -> dict:
    """Create a task from webhook payload."""
    # Extract task data based on provider
    if webhook.provider == "github":
        # Handle GitHub issues
        if "issue" in payload:
            issue = payload["issue"]
            title = issue.get("title", "Untitled")
            body = issue.get("body", "")
            number = issue.get("number")
            
            # Create task
            task_id = str(uuid4())
            project_id = config.get("project_id", "inbox")
            
            task = TaskModel(
                id=task_id,
                title=f"[GitHub #{number}] {title}",
                notes=body,
                status="inbox",
                project_id=project_id,
                external_source="github",
                external_id=str(number),
                github_issue_number=number,
            )
            
            db.add(task)
            await db.commit()
            
            return {
                "status": "created",
                "task_id": task_id,
                "title": title,
                "source": f"github_issue_{number}"
            }
    
    # Generic task creation from custom webhooks
    title = payload.get("title") or payload.get("name") or "Webhook Task"
    description = payload.get("description") or payload.get("body") or ""
    
    task_id = str(uuid4())
    project_id = config.get("project_id", "inbox")
    
    task = TaskModel(
        id=task_id,
        title=title,
        notes=description,
        status="inbox",
        project_id=project_id,
        external_source=webhook.provider,
        external_id=payload.get("id"),
    )
    
    db.add(task)
    await db.commit()
    
    return {
        "status": "created",
        "task_id": task_id,
        "title": title
    }


async def trigger_agent_from_webhook(
    db: AsyncSession,
    webhook: WebhookRegistrationModel,
    payload: dict,
    config: dict
) -> dict:
    """Trigger an agent action from webhook (placeholder for future implementation)."""
    # This would integrate with the orchestrator to spawn an agent task
    agent_type = config.get("agent_type", "programmer")
    
    logger.info(f"Would trigger {agent_type} agent with payload: {payload}")
    
    return {
        "status": "queued",
        "agent_type": agent_type,
        "note": "Agent triggering not yet implemented"
    }


async def update_project_from_webhook(
    db: AsyncSession,
    webhook: WebhookRegistrationModel,
    payload: dict,
    config: dict
) -> dict:
    """Update project from webhook payload (placeholder for future implementation)."""
    project_id = config.get("project_id")
    
    if not project_id:
        return {"status": "error", "reason": "No project_id in config"}
    
    result = await db.execute(
        select(ProjectModel).where(ProjectModel.id == project_id)
    )
    project = result.scalar_one_or_none()
    
    if not project:
        return {"status": "error", "reason": "Project not found"}
    
    # Update fields based on payload
    updated_fields = []
    if "title" in payload:
        project.title = payload["title"]
        updated_fields.append("title")
    if "notes" in payload:
        project.notes = payload["notes"]
        updated_fields.append("notes")
    
    if updated_fields:
        project.updated_at = datetime.now(timezone.utc)
        await db.commit()
    
    return {
        "status": "updated",
        "project_id": project_id,
        "updated_fields": updated_fields
    }
