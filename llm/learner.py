"""Learning extraction helpers for memory-backed workflows."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any


@dataclass
class LearningExtract:
    title: str
    learned: str
    why: str = ""
    how_to_apply: str = ""
    related_skills: list[str] | None = None
    worth_remembering: bool = False


class LearningExtractor:
    def __init__(self, chat_handler: Any):
        self.chat_handler = chat_handler

    def extract(
        self,
        *,
        original_item_description: str,
        final_used_prompt: str,
        score: int,
        strengths: list[str],
        issues: list[str],
        related_skills: list[str],
    ) -> LearningExtract:
        prompt = f"""你是一个设计知识提炼助手。请从一次任务中提炼可复用经验。

原始需求：{original_item_description}
最终 Prompt：{final_used_prompt}
自评分：{score}/10
优点：{strengths}
问题：{issues}

只输出 JSON：
{{
  "title": "15字以内标题",
  "learned": "一句话描述学到什么",
  "why": "为什么值得记住",
  "how_to_apply": "以后怎么应用",
  "related_skills": {related_skills},
  "worth_remembering": true
}}"""
        try:
            raw = self.chat_handler.get_ai_response(prompt, temperature=0.2)
            data = self._extract_json(raw)
            return LearningExtract(
                title=str(data.get("title") or ""),
                learned=str(data.get("learned") or ""),
                why=str(data.get("why") or ""),
                how_to_apply=str(data.get("how_to_apply") or ""),
                related_skills=list(data.get("related_skills") or related_skills),
                worth_remembering=bool(data.get("worth_remembering")),
            )
        except Exception:
            return LearningExtract(title="", learned="", related_skills=related_skills)

    def _extract_json(self, raw: str) -> dict[str, Any]:
        text = (raw or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
            text = re.sub(r"\s*```$", "", text)
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}", text)
            if not match:
                return {}
            try:
                parsed = json.loads(match.group(0))
                return parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                return {}
