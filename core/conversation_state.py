"""Conversation state for clarification-aware task processing."""

from __future__ import annotations

from dataclasses import dataclass, field
import time

from core.intent import ItemHint


@dataclass
class ClarificationOption:
    label: str
    value: str


@dataclass
class ClarificationRequest:
    id: str
    item_id: str
    question: str
    options: list[ClarificationOption] = field(default_factory=list)
    item: ItemHint | None = None
    created_at: float = field(default_factory=time.time)
    ttl_seconds: int = 300

    def is_expired(self, now: float | None = None) -> bool:
        current = now if now is not None else time.time()
        return current - self.created_at > self.ttl_seconds


@dataclass
class ConversationState:
    chat_id: str
    pending_clarification: ClarificationRequest | None = None
    active_items: dict[str, ItemHint] = field(default_factory=dict)
    memory_cache: dict[str, str] = field(default_factory=dict)


class ConversationStateManager:
    def __init__(self):
        self._states: dict[str, ConversationState] = {}

    def get(self, chat_id: str) -> ConversationState:
        if chat_id not in self._states:
            self._states[chat_id] = ConversationState(chat_id=chat_id)
        return self._states[chat_id]

    def get_pending(self, chat_id: str) -> ClarificationRequest | None:
        state = self.get(chat_id)
        pending = state.pending_clarification
        if pending and pending.is_expired():
            state.pending_clarification = None
            return None
        return pending

    def set_pending(self, chat_id: str, request: ClarificationRequest) -> None:
        self.get(chat_id).pending_clarification = request

    def clear_pending(self, chat_id: str) -> None:
        self.get(chat_id).pending_clarification = None

    def create_subject_count_clarification(self, chat_id: str, item: ItemHint) -> ClarificationRequest:
        request = ClarificationRequest(
            id=f"clar_{int(time.time() * 1000)}",
            item_id=item.id,
            item=item,
            question="你想要一张图里包含这些对象，还是生成多张独立图片？",
            options=[
                ClarificationOption(label="一张合成图", value="compose"),
                ClarificationOption(label="多张独立图", value="separate"),
            ],
        )
        self.set_pending(chat_id, request)
        return request

    def resolve_pending(self, chat_id: str, reply: str) -> ItemHint | None:
        pending = self.get_pending(chat_id)
        if not pending or not pending.item:
            return None

        text = (reply or "").strip()
        resolved = ItemHint(
            id=pending.item.id,
            description=self._merge_reply(pending.item.description, text),
            mode=pending.item.mode,
            reference_indices=list(pending.item.reference_indices),
            ambiguity_flags=[],
        )
        self.clear_pending(chat_id)
        return resolved

    def _merge_reply(self, description: str, reply: str) -> str:
        normalized = reply.lower()
        if any(keyword in normalized for keyword in ("独立", "分开", "多张", "separate", "each")):
            return f"{description}。用户已澄清：生成多张独立结果。"
        if any(keyword in normalized for keyword in ("一张", "合成", "一起", "compose", "single")):
            return f"{description}。用户已澄清：合成到一张结果中。"
        return f"{description}。用户澄清：{reply}"
