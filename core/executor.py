"""Execution runner for compiled tasks."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from llm.compiler import ExecutionPlan, ExecutionTask


class ExecutionRunner:
    def __init__(self, processor: Any):
        self.processor = processor

    def execute_plan(
        self,
        plan: ExecutionPlan,
        *,
        chat_id: str,
        original_prompt: str,
        file_exts: list[str] | None = None,
    ) -> dict:
        if not plan.tasks:
            return self.processor._normalize_result({"text": "没有可执行的任务。"})
        if len(plan.tasks) == 1:
            return self.execute_task(
                plan.tasks[0],
                chat_id=chat_id,
                original_prompt=original_prompt,
                file_exts=file_exts,
            )
        if plan.execution_mode == "parallel":
            return self._execute_parallel(
                plan,
                chat_id=chat_id,
                original_prompt=original_prompt,
                file_exts=file_exts,
            )
        return self._execute_sequential(
            plan,
            chat_id=chat_id,
            original_prompt=original_prompt,
            file_exts=file_exts,
        )

    def execute_task(
        self,
        task: ExecutionTask,
        *,
        chat_id: str,
        original_prompt: str,
        file_exts: list[str] | None = None,
    ) -> dict:
        image_paths = [str(path) for path in task.image_paths]
        file_paths = [str(path) for path in task.file_paths]
        retry_context = task.context.retry_context
        result = self.processor.skills_loader.execute_skill(
            name=task.skill,
            message=task.message,
            chat_id=chat_id,
            processor=self.processor,
            has_images=bool(image_paths),
            image_paths=image_paths,
            has_files=bool(file_paths),
            file_paths=file_paths,
            file_exts=file_exts,
            use_pro=task.context.use_pro,
            task_mode=task.mode,
            current_attempt=retry_context.attempt if retry_context else 0,
            original_prompt=original_prompt,
        )
        normalized = self.processor._normalize_result(result)
        normalized["tool_trace"] = [
            {
                "task_id": task.id,
                "item_id": task.item_id,
                "skill": task.skill,
                "mode": task.mode,
                "image_count": len(image_paths),
                "file_count": len(file_paths),
                "text_preview": (normalized.get("text") or "")[:120],
            }
        ]
        return normalized

    def _execute_parallel(
        self,
        plan: ExecutionPlan,
        *,
        chat_id: str,
        original_prompt: str,
        file_exts: list[str] | None,
    ) -> dict:
        results: list[tuple[int, dict]] = []
        with ThreadPoolExecutor(max_workers=min(4, len(plan.tasks))) as executor:
            futures = {
                executor.submit(
                    self.execute_task,
                    task,
                    chat_id=chat_id,
                    original_prompt=original_prompt,
                    file_exts=file_exts,
                ): index
                for index, task in enumerate(plan.tasks)
            }
            for future in as_completed(futures):
                index = futures[future]
                try:
                    results.append((index, self.processor._normalize_result(future.result())))
                except Exception as exc:
                    results.append((index, self.processor._normalize_result({"text": str(exc)})))
        results.sort(key=lambda item: item[0])
        return self._aggregate_results([result for _, result in results], plan)

    def _execute_sequential(
        self,
        plan: ExecutionPlan,
        *,
        chat_id: str,
        original_prompt: str,
        file_exts: list[str] | None,
    ) -> dict:
        results = [
            self.execute_task(
                task,
                chat_id=chat_id,
                original_prompt=original_prompt,
                file_exts=file_exts,
            )
            for task in plan.tasks
        ]
        return self._aggregate_results(results, plan)

    def _aggregate_results(self, results: list[dict], plan: ExecutionPlan) -> dict:
        image_paths: list[str] = []
        text_parts: list[str] = []
        trace: list[dict[str, Any]] = []
        file_path = None
        pdf_path = None

        for result in results:
            for path in self.processor._collect_image_paths(result):
                if path not in image_paths:
                    image_paths.append(path)
            text = (result.get("text") or "").strip()
            if text and not self._looks_like_image_info_only(text):
                text_parts.append(text)
            trace.extend(result.get("tool_trace") or [])
            file_path = file_path or result.get("file_path")
            pdf_path = pdf_path or result.get("pdf_path")

        text = plan.summary or f"已完成 {len(results)} 个任务。"
        if image_paths:
            text = f"已完成 {len(image_paths)} 张图片。"
            image_info = self.processor._format_multiple_image_generation_info(
                image_paths,
                use_pro=any(task.context.use_pro for task in plan.tasks),
            )
            if image_info:
                text += f"\n\n{image_info}"
        elif text_parts:
            text = "\n\n".join(text_parts)

        return {
            "text": text,
            "image_path": image_paths[0] if image_paths else None,
            "image_paths": image_paths,
            "file_path": file_path,
            "pdf_path": pdf_path,
            "needs_reflection": False,
            "reflection_context": None,
            "tool_trace": trace,
        }

    def _looks_like_image_info_only(self, text: str) -> bool:
        lowered = text.lower()
        return "png" in lowered and "generated_images" in lowered
