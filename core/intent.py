"""Local intent parsing for the compile-then-execute pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Literal


IntentAction = Literal["decompose", "pass_through", "clarify_reply"]
ItemMode = Literal["generate", "edit", "compose", "analyze", "unknown"]


@dataclass
class ItemHint:
    id: str
    description: str
    mode: ItemMode = "unknown"
    reference_indices: list[int] = field(default_factory=list)
    ambiguity_flags: list[str] = field(default_factory=list)


@dataclass
class IntentSignal:
    action: IntentAction
    items: list[ItemHint] = field(default_factory=list)
    parent_item_id: str | None = None


class IntentParser:
    """A lightweight, non-LLM parser for deciding whether compilation is needed."""

    _split_keywords = (
        "都",
        "每个",
        "每张",
        "分别",
        "各自",
        "each",
        "every",
        "separately",
        "respectively",
    )
    _new_request_keywords = (
        "画",
        "生成",
        "做",
        "改",
        "分析",
        "总结",
        "设计",
        "create",
        "generate",
        "draw",
        "edit",
        "analyze",
        "make",
    )

    def parse(
        self,
        message: str,
        *,
        primary_skill: str,
        has_images: bool = False,
        num_images: int = 0,
        has_files: bool = False,
        num_files: int = 0,
        has_pending_clarification: bool = False,
    ) -> IntentSignal:
        text = (message or "").strip()
        if has_pending_clarification and self.looks_like_clarification_reply(text):
            return IntentSignal(action="clarify_reply")

        mode = self._mode_for(primary_skill, has_images=has_images, has_files=has_files)
        if self._should_decompose(
            text,
            primary_skill=primary_skill,
            num_images=num_images,
            num_files=num_files,
        ):
            return IntentSignal(
                action="decompose",
                items=self._build_item_hints(
                    text,
                    mode=mode,
                    num_images=num_images,
                    num_files=num_files,
                ),
            )

        return IntentSignal(action="pass_through")

    def looks_like_clarification_reply(self, message: str) -> bool:
        text = (message or "").strip().lower()
        if not text:
            return False
        if len(text) > 40:
            return False
        return not any(keyword in text for keyword in self._new_request_keywords)

    def _should_decompose(
        self,
        message: str,
        *,
        primary_skill: str,
        num_images: int,
        num_files: int,
    ) -> bool:
        text = (message or "").strip()
        if primary_skill == "image_gen" and (num_images >= 2 or num_files >= 2):
            return True
        if self._has_split_signal(text):
            return True
        if self._requested_count(text) > 1:
            return True
        return False

    def _build_item_hints(
        self,
        message: str,
        *,
        mode: ItemMode,
        num_images: int,
        num_files: int,
    ) -> list[ItemHint]:
        text = (message or "").strip()
        image_batch = num_images >= 2 and self._has_batch_signal(text)
        if image_batch:
            return [
                ItemHint(
                    id=f"item_{index + 1:03d}",
                    description=text or f"处理第 {index + 1} 张图片",
                    mode="edit" if mode in {"generate", "unknown"} else mode,
                    reference_indices=[index],
                )
                for index in range(num_images)
            ]

        split_descriptions = self._split_descriptions(text)
        if len(split_descriptions) > 1:
            return [
                ItemHint(
                    id=f"item_{index + 1:03d}",
                    description=description,
                    mode=mode,
                )
                for index, description in enumerate(split_descriptions)
            ]

        count = self._requested_count(text)
        ambiguity_flags = ["subject_count"] if count > 1 and not self._has_explicit_variants(text) else []
        if count > 1 and not ambiguity_flags:
            return [
                ItemHint(
                    id=f"item_{index + 1:03d}",
                    description=text,
                    mode=mode,
                )
                for index in range(count)
            ]

        return [
            ItemHint(
                id="item_001",
                description=text,
                mode=mode,
                reference_indices=list(range(num_files)) if num_files >= 2 else [],
                ambiguity_flags=ambiguity_flags,
            )
        ]

    def _mode_for(self, skill: str, *, has_images: bool, has_files: bool) -> ItemMode:
        if skill == "image_gen":
            return "edit" if has_images else "generate"
        if skill == "image_understanding":
            return "analyze"
        if has_files:
            return "analyze"
        return "unknown"

    def _has_split_signal(self, text: str) -> bool:
        if any(keyword in text for keyword in self._split_keywords):
            return True
        if len(self._split_descriptions(text)) > 1:
            return True
        return bool(re.search(r"one\s+.+\s+and\s+one\s+", text, flags=re.IGNORECASE))

    def _has_batch_signal(self, text: str) -> bool:
        batch_keywords = ("都", "每张", "每个", "分别", "each", "every", "separately")
        return any(keyword in text.lower() for keyword in batch_keywords)

    def _has_explicit_variants(self, text: str) -> bool:
        if re.search(r"一个.+一个|一张.+一张", text):
            return True
        if re.search(r"one\s+.+\s+one\s+", text, flags=re.IGNORECASE):
            return True
        return any(keyword in text for keyword in ("分别", "各自", "不同", "variants", "versions"))

    def _requested_count(self, text: str) -> int:
        if not text:
            return 1
        digit_match = re.search(r"([2-9]|1\d)\s*(?:张|个|幅|份|images?|pictures?|pics?|variants?|versions?)", text, flags=re.IGNORECASE)
        if digit_match:
            return int(digit_match.group(1))
        chinese_numbers = {
            "两": 2,
            "二": 2,
            "俩": 2,
            "三": 3,
            "四": 4,
            "五": 5,
            "六": 6,
            "七": 7,
            "八": 8,
            "九": 9,
            "十": 10,
        }
        chinese_match = re.search(r"([两二俩三四五六七八九十])\s*(?:张|个|幅|份)", text)
        if chinese_match:
            return chinese_numbers.get(chinese_match.group(1), 1)
        return 1

    def _split_descriptions(self, text: str) -> list[str]:
        if not text:
            return []
        matches = re.findall(r"(?:一个|一张)\s*([^，,；;。]+)", text)
        if len(matches) > 1:
            prefix = re.split(r"一个|一张", text, maxsplit=1)[0].strip("，,；;。 ")
            return [self._join_prefix(prefix, match.strip()) for match in matches if match.strip()]

        english_matches = re.findall(r"(?:one|a|an)\s+([^,;.]+)", text, flags=re.IGNORECASE)
        if len(english_matches) > 1:
            return [match.strip() for match in english_matches if match.strip()]
        return []

    def _join_prefix(self, prefix: str, fragment: str) -> str:
        if not prefix:
            return fragment
        if fragment.startswith(prefix):
            return fragment
        return f"{prefix}{fragment}"
