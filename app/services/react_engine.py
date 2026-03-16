from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.models.schemas import ChatMessage


@dataclass(frozen=True)
class ReActResult:
    answer: str
    action: str | None = None
    action_input: dict[str, Any] | None = None


class ReActEngine:
    """
    Minimal ReAct-style reasoning scaffold.

    This is intentionally pluggable: replace `plan_and_act` with an LLM-backed
    implementation and tool registry.
    """

    async def plan_and_act(
        self,
        *,
        subject: str,
        message: str,
        history: list[ChatMessage],
        action: str | None = None,
        action_input: dict[str, Any] | None = None,
    ) -> ReActResult:
        # Stub: echo-like behavior with light context usage.
        if action:
            msg = (
                f"[ReAct] Received action request '{action}'. "
                "I will proceed after confirmation if required."
            )
            return ReActResult(
                answer=msg,
                action=action,
                action_input=action_input or {},
            )
        context = history[-4:] if history else []
        ctx = " | ".join([f"{m.role}:{m.content[:60]}" for m in context])
        answer = f"[ReAct] {message}"
        if ctx:
            answer += f"\n[context] {ctx}"
        return ReActResult(answer=answer)
