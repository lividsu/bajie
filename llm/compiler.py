"""Execution compiler for Bajie 2.0."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from typing import Any, Literal

from core.intent import IntentSignal, ItemHint


ExecutionMode = Literal["parallel", "sequential", "single"]


@dataclass
class RetryContext:
    attempt: int = 0
    max_attempts: int = 2
    previous_issues: list[str] = field(default_factory=list)
    previous_strengths: list[str] = field(default_factory=list)


@dataclass
class TaskContext:
    use_pro: bool = False
    prefer_aspect_ratio: str | None = None
    prefer_output_count: int = 1
    style_hints: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    memory_snippets: list[str] = field(default_factory=list)
    retry_context: RetryContext | None = None


@dataclass
class ExecutionTask:
    id: str
    item_id: str
    skill: str
    mode: str
    message: str
    image_paths: list[Path] = field(default_factory=list)
    file_paths: list[Path] = field(default_factory=list)
    context: TaskContext = field(default_factory=TaskContext)


@dataclass
class ExecutionPlan:
    tasks: list[ExecutionTask]
    execution_mode: ExecutionMode = "single"
    summary: str = ""


class ExecutionCompiler:
    def __init__(self, chat_handler: Any, skills_loader: Any):
        self.chat_handler = chat_handler
        self.skills_loader = skills_loader

    def compile(
        self,
        *,
        intent: IntentSignal,
        original_message: str,
        primary_skill: str,
        image_paths: list[str] | None,
        file_paths: list[str] | None,
        use_pro: bool,
        memory_snippets: list[str] | None = None,
    ) -> ExecutionPlan:
        memory_snippets = memory_snippets or []
        try:
            plan = self._compile_with_llm(
                intent=intent,
                original_message=original_message,
                primary_skill=primary_skill,
                image_paths=image_paths or [],
                file_paths=file_paths or [],
                use_pro=use_pro,
                memory_snippets=memory_snippets,
            )
            if plan.tasks:
                return plan
        except Exception as exc:
            print(f"Compiler LLM fallback: {exc}")

        return self._compile_locally(
            intent=intent,
            original_message=original_message,
            primary_skill=primary_skill,
            image_paths=image_paths or [],
            file_paths=file_paths or [],
            use_pro=use_pro,
            memory_snippets=memory_snippets,
        )

    def _compile_with_llm(
        self,
        *,
        intent: IntentSignal,
        original_message: str,
        primary_skill: str,
        image_paths: list[str],
        file_paths: list[str],
        use_pro: bool,
        memory_snippets: list[str],
    ) -> ExecutionPlan:
        items_json = json.dumps([self._item_to_dict(item) for item in intent.items], ensure_ascii=False)
        prompt = f"""你是 Bajie 的任务编译器。你的唯一职责是把已经消歧后的 ItemHint 编译成 ExecutionTask JSON。

规则：
- 只输出 JSON，不要解释。
- 不要调用工具，不要生成最终内容。
- 每个 task.message 必须是独立、完整、无歧义的执行指令。
- 多张参考图需要分别处理时，每个 task 只绑定自己的 image_indices。
- 如果只是单一明确请求，输出一个 task。

可用 skills：
{self.skills_loader.build_skills_summary()}

原始用户消息（仅供理解，不要直接透传有歧义的原文）：{original_message}
首选 skill：{primary_skill}
ItemHint JSON：{items_json}
图片数量：{len(image_paths)}
文件数量：{len(file_paths)}
Memory：
{chr(10).join(memory_snippets) if memory_snippets else "无"}

输出格式：
{{
  "execution_mode": "parallel|sequential|single",
  "summary": "一句话摘要",
  "tasks": [
    {{
      "id": "task_001",
      "item_id": "item_001",
      "skill": "{primary_skill}",
      "mode": "generate|edit|batch_edit|compose|analyze|chat",
      "message": "完整执行指令",
      "image_indices": [0],
      "file_indices": [],
      "context": {{
        "prefer_aspect_ratio": null,
        "prefer_output_count": 1,
        "style_hints": [],
        "constraints": []
      }}
    }}
  ]
}}"""
        raw = self.chat_handler.get_ai_response(prompt, temperature=0.1)
        parsed = self._extract_json(raw)
        return self._plan_from_dict(
            parsed,
            image_paths=image_paths,
            file_paths=file_paths,
            use_pro=use_pro,
            memory_snippets=memory_snippets,
            fallback_skill=primary_skill,
        )

    def _compile_locally(
        self,
        *,
        intent: IntentSignal,
        original_message: str,
        primary_skill: str,
        image_paths: list[str],
        file_paths: list[str],
        use_pro: bool,
        memory_snippets: list[str],
    ) -> ExecutionPlan:
        items = intent.items or [
            ItemHint(
                id="item_001",
                description=original_message,
                mode="edit" if image_paths else "generate",
            )
        ]
        tasks: list[ExecutionTask] = []
        for index, item in enumerate(items, start=1):
            selected_images = self._select_paths(image_paths, item.reference_indices)
            selected_files = self._select_paths(file_paths, item.reference_indices)
            if not selected_images and len(items) == 1:
                selected_images = [Path(path) for path in image_paths]
            if not selected_files and len(items) == 1:
                selected_files = [Path(path) for path in file_paths]
            task_skill = self._skill_for(primary_skill, item, selected_images, selected_files)
            tasks.append(
                ExecutionTask(
                    id=f"task_{index:03d}",
                    item_id=item.id,
                    skill=task_skill,
                    mode=self._mode_for(item, selected_images, selected_files),
                    message=self._message_for(item.description or original_message, memory_snippets),
                    image_paths=selected_images,
                    file_paths=selected_files,
                    context=TaskContext(
                        use_pro=use_pro,
                        memory_snippets=memory_snippets,
                    ),
                )
            )
        mode: ExecutionMode = "single"
        if len(tasks) > 1:
            mode = "parallel" if all(task.skill == tasks[0].skill for task in tasks) else "sequential"
        return ExecutionPlan(tasks=tasks, execution_mode=mode, summary=f"Compiled {len(tasks)} task(s)")

    def _plan_from_dict(
        self,
        data: dict[str, Any],
        *,
        image_paths: list[str],
        file_paths: list[str],
        use_pro: bool,
        memory_snippets: list[str],
        fallback_skill: str,
    ) -> ExecutionPlan:
        raw_tasks = data.get("tasks") if isinstance(data, dict) else None
        if not isinstance(raw_tasks, list):
            return ExecutionPlan(tasks=[])
        tasks: list[ExecutionTask] = []
        for index, raw_task in enumerate(raw_tasks, start=1):
            if not isinstance(raw_task, dict):
                continue
            context = raw_task.get("context") if isinstance(raw_task.get("context"), dict) else {}
            tasks.append(
                ExecutionTask(
                    id=str(raw_task.get("id") or f"task_{index:03d}"),
                    item_id=str(raw_task.get("item_id") or "item_001"),
                    skill=str(raw_task.get("skill") or fallback_skill),
                    mode=str(raw_task.get("mode") or "generate"),
                    message=str(raw_task.get("message") or ""),
                    image_paths=self._select_paths(image_paths, raw_task.get("image_indices") or []),
                    file_paths=self._select_paths(file_paths, raw_task.get("file_indices") or []),
                    context=TaskContext(
                        use_pro=use_pro,
                        prefer_aspect_ratio=context.get("prefer_aspect_ratio"),
                        prefer_output_count=int(context.get("prefer_output_count") or 1),
                        style_hints=list(context.get("style_hints") or []),
                        constraints=list(context.get("constraints") or []),
                        memory_snippets=memory_snippets,
                    ),
                )
            )
        execution_mode = str(data.get("execution_mode") or "single")
        if execution_mode not in {"parallel", "sequential", "single"}:
            execution_mode = "single"
        return ExecutionPlan(
            tasks=tasks,
            execution_mode=execution_mode,  # type: ignore[arg-type]
            summary=str(data.get("summary") or ""),
        )

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

    def _select_paths(self, paths: list[str], indices: Any) -> list[Path]:
        if not isinstance(indices, list):
            return []
        selected = []
        for raw_index in indices:
            try:
                index = int(raw_index)
            except (TypeError, ValueError):
                continue
            if 0 <= index < len(paths):
                selected.append(Path(paths[index]))
        return selected

    def _skill_for(
        self,
        primary_skill: str,
        item: ItemHint,
        image_paths: list[Path],
        file_paths: list[Path],
    ) -> str:
        if primary_skill:
            return primary_skill
        if image_paths:
            return "image_gen" if item.mode == "edit" else "image_understanding"
        if file_paths:
            return "pdf"
        return "general"

    def _mode_for(self, item: ItemHint, image_paths: list[Path], file_paths: list[Path]) -> str:
        if item.mode != "unknown":
            return item.mode
        if image_paths:
            return "edit"
        if file_paths:
            return "analyze"
        return "chat"

    def _message_for(self, description: str, memory_snippets: list[str]) -> str:
        message = description.strip()
        if memory_snippets:
            message += "\n\nMemory context:\n" + "\n\n".join(memory_snippets)
        return message

    def _item_to_dict(self, item: ItemHint) -> dict[str, Any]:
        return {
            "id": item.id,
            "description": item.description,
            "mode": item.mode,
            "reference_indices": item.reference_indices,
            "ambiguity_flags": item.ambiguity_flags,
        }
