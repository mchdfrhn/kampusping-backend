import json
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError

from app.api.deps import ClockDep, DbSession, require_permissions
from app.core.config import settings
from app.core.constants import APPLICATION_ERROR, UNAUTHORIZED, VALIDATION_ERROR
from app.core.exceptions import AppException
from app.core.permissions import SETTINGS_READ, SETTINGS_UPDATE
from app.modules.audit.service import record_audit
from app.modules.messages.service import MessageService
from app.modules.settings.service import SettingsService
from app.modules.users.model import User

from .constants import (
    AUDIT_ENTITY_WHATSAPP,
    AUDIT_WHATSAPP_LOGOUT,
    EVENT_STATUS,
    MAX_WEBHOOK_BODY_BYTES,
    MSG_BODY_TOO_LARGE,
    MSG_INVALID_PAYLOAD,
    MSG_INVALID_SIGNATURE,
    MSG_LOGOUT_FAILED,
    SIGNATURE_HEADER,
)
from .deps import current_provider
from .provider import ProviderError, ProviderState, WhatsAppProvider
from .schema import LogoutRead, WebhookAccepted, WebhookStatusEvent, WhatsAppStatusRead
from .signature import verify_signature

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/whatsapp", tags=["WhatsApp"])
webhook_router = APIRouter(prefix="/webhooks", tags=["Webhooks"])

CanRead = Annotated[User, Depends(require_permissions(SETTINGS_READ))]
CanUpdate = Annotated[User, Depends(require_permissions(SETTINGS_UPDATE))]
Provider = Annotated[WhatsAppProvider | None, Depends(current_provider)]

_warned_missing_secret = False


@router.get("/status", response_model=WhatsAppStatusRead, summary="WhatsApp connection status")
async def whatsapp_status(db: DbSession, _: CanRead, provider: Provider) -> WhatsAppStatusRead:
    state: ProviderState = "disconnected"
    qr: str | None = None
    if provider is not None:  # None: the provider cannot be built, reported as disconnected
        try:
            current = await provider.status()
            state, qr = current.state, current.qr
        except Exception as exc:
            logger.warning("WhatsApp status check failed: %s", type(exc).__name__)

    row = await SettingsService(db).get()
    await db.commit()  # persists the default settings row when it was just created
    return WhatsAppStatusRead(state=state, qr=qr, paused_reason=row.paused_reason)


@router.post("/logout", response_model=LogoutRead, summary="Disconnect the WhatsApp session")
async def whatsapp_logout(db: DbSession, actor: CanUpdate, provider: Provider) -> LogoutRead:
    if provider is None:
        raise AppException(MSG_LOGOUT_FAILED, status_code=502, code=APPLICATION_ERROR)
    try:
        await provider.logout()
    except ProviderError as exc:
        logger.warning("WhatsApp logout failed: %s", type(exc).__name__)
        raise AppException(MSG_LOGOUT_FAILED, status_code=502, code=APPLICATION_ERROR) from exc
    await record_audit(
        db, actor=actor.email, action=AUDIT_WHATSAPP_LOGOUT, entity=AUDIT_ENTITY_WHATSAPP
    )
    await db.commit()
    return LogoutRead()


def _invalid_signature() -> AppException:
    # One generic answer for every reason, so a caller learns nothing about why it failed.
    return AppException(MSG_INVALID_SIGNATURE, status_code=401, code=UNAUTHORIZED)


def _too_large() -> AppException:
    return AppException(MSG_BODY_TOO_LARGE, status_code=413, code=APPLICATION_ERROR)


def _invalid_payload() -> AppException:
    return AppException(MSG_INVALID_PAYLOAD, status_code=422, code=VALIDATION_ERROR)


async def _read_limited_body(request: Request) -> bytes:
    """The raw body (the HMAC covers the exact bytes); 413 as soon as it exceeds the limit."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_WEBHOOK_BODY_BYTES:
        raise _too_large()
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_WEBHOOK_BODY_BYTES:
            raise _too_large()
    return bytes(body)


def _parse_event(body: bytes) -> dict[str, Any]:
    try:
        event = json.loads(body)
    except (ValueError, RecursionError) as exc:
        raise _invalid_payload() from exc
    if not isinstance(event, dict):
        raise _invalid_payload()
    return event


@webhook_router.post(
    "/whatsapp",
    response_model=WebhookAccepted,
    status_code=202,
    summary="Receive gateway events (signed with X-Signature)",
)
async def whatsapp_webhook(request: Request, db: DbSession, clock: ClockDep) -> WebhookAccepted:
    global _warned_missing_secret

    configured = settings.wa_webhook_secret
    secret = configured.get_secret_value() if configured else ""
    if not secret:
        # Never accept an unsigned webhook, however the request looks.
        if not _warned_missing_secret:
            _warned_missing_secret = True
            logger.warning("WA_WEBHOOK_SECRET is not configured: rejecting every webhook call")
        raise _invalid_signature()

    body = await _read_limited_body(request)
    if not verify_signature(secret, body, request.headers.get(SIGNATURE_HEADER)):
        raise _invalid_signature()

    event = _parse_event(body)
    if event.get("type") == EVENT_STATUS:
        try:
            receipt = WebhookStatusEvent.model_validate(event)
        except ValidationError as exc:
            raise _invalid_payload() from exc
        await MessageService(db, clock).apply_receipt(receipt.provider_message_id, receipt.state)
    # "connection" needs no persistence, "inbound" replies arrive in milestone M3, and unknown
    # types are acknowledged too so the gateway never retries them.
    return WebhookAccepted()
