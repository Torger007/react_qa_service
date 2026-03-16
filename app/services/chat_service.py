from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from redis.asyncio import Redis

from app.core.config import settings
from app.core.redis_client import key
from app.models.response import ConfirmChallengeResponse
from app.models.schemas import ChatMessage
from app.services.react_engine import ReActEngine
from app.services.session_manager import append_message, get_history


@dataclass(frozen=True)
class ChatOutcome:
    answer: str
    history: list[ChatMessage]
    confirm_challenge: ConfirmChallengeResponse | None = None


def _confirm_key(subject: str, session_id: UUID, action: str) -> str:
    return key("confirm", subject, str(session_id), action)


def _is_sensitive_action(action: str) -> bool:
    # Extend with richer policy (RBAC, allowlists, risk scoring, etc.)
    return action.lower() in {"delete", "refund", "transfer", "export", "admin", "reset_password"}


async def _issue_confirm_token(
    r: Redis, *, subject: str, session_id: UUID, action: str, action_input: dict[str, Any] | None
) -> ConfirmChallengeResponse:
    token = secrets.token_urlsafe(24)
    k = _confirm_key(subject, session_id, action)
    payload = {"token": token, "action_input": action_input or {}}
    await r.set(k, json.dumps(payload, ensure_ascii=False), ex=settings.confirm_ttl_seconds)
    return ConfirmChallengeResponse(
        confirm_token=token,
        expires_in_seconds=settings.confirm_ttl_seconds,
        reason="Sensitive action requires confirmation",
        details={"action": action, "action_input": action_input or {}},
    )


async def _consume_confirm_token(
    r: Redis, *, subject: str, session_id: UUID, action: str, confirm_token: str
) -> dict[str, Any] | None:
    k = _confirm_key(subject, session_id, action)
    raw = await r.get(k)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("token") != confirm_token:
        return None
    await r.delete(k)
    action_input = data.get("action_input")
    return action_input if isinstance(action_input, dict) else {}


class ChatService:
    def __init__(self, react_engine: ReActEngine):
        self.react_engine = react_engine

    async def chat(
        self,
        *,
        redis: Redis,
        subject: str,
        session_id: UUID,
        message: str,
        action: str | None,
        action_input: dict[str, Any] | None,
        confirm_token: str | None,
    ) -> ChatOutcome:
        history = await get_history(redis, session_id)

        # Always persist user message first (so multi-worker responses are consistent)
        user_msg = ChatMessage(role="user", content=message)
        await append_message(redis, session_id, user_msg)

        # Confirmation workflow for sensitive actions
        if action and _is_sensitive_action(action):
            if not confirm_token:
                challenge = await _issue_confirm_token(
                    redis,
                    subject=subject,
                    session_id=session_id,
                    action=action,
                    action_input=action_input,
                )
                assistant_msg = ChatMessage(
                    role="assistant",
                    content=f"Action '{action}' is sensitive. Please confirm using confirm_token.",
                )
                await append_message(redis, session_id, assistant_msg)
                history2 = await get_history(redis, session_id)
                return ChatOutcome(
                    answer=assistant_msg.content, history=history2, confirm_challenge=challenge
                )

            verified_action_input = await _consume_confirm_token(
                redis,
                subject=subject,
                session_id=session_id,
                action=action,
                confirm_token=confirm_token,
            )
            if verified_action_input is None:
                assistant_msg = ChatMessage(
                    role="assistant", content="Invalid or expired confirm_token."
                )
                await append_message(redis, session_id, assistant_msg)
                history2 = await get_history(redis, session_id)
                return ChatOutcome(answer=assistant_msg.content, history=history2)
            action_input = verified_action_input

        # ReAct reasoning
        result = await self.react_engine.plan_and_act(
            subject=subject,
            message=message,
            history=history,
            action=action,
            action_input=action_input,
        )
        assistant_msg = ChatMessage(role="assistant", content=result.answer)
        await append_message(redis, session_id, assistant_msg)
        history2 = await get_history(redis, session_id)
        return ChatOutcome(answer=result.answer, history=history2)
