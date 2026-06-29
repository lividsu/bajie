from typing import Any
from .base import Tool, ToolSpec


class ExecuteSkillTool(Tool):
    def __init__(self):
        self.spec = ToolSpec(
            name="execute_skill",
            description="执行一个已有 skill，并返回结构化结果",
            parameters={
                "skill_name": "string",
                "message": "string",
                "reason": "string"
            }
        )

    def execute(self, args: dict[str, Any], runtime: dict[str, Any]) -> dict[str, Any]:
        skill_name = str(args.get("skill_name", "")).strip()
        if not skill_name:
            raise ValueError("skill_name is required")
        message = str(args.get("message", runtime.get("message", "")))
        skills_loader = runtime["skills_loader"]
        result = skills_loader.execute_skill(
            name=skill_name,
            message=message,
            chat_id=runtime["chat_id"],
            processor=runtime["processor"],
            has_images=runtime.get("has_images", False),
            image_paths=runtime.get("image_paths"),
            has_files=runtime.get("has_files", False),
            file_paths=runtime.get("file_paths"),
            file_exts=runtime.get("file_exts"),
            use_pro=runtime.get("use_pro", False),
            current_attempt=runtime.get("current_attempt", 0),
            original_prompt=runtime.get("original_prompt", runtime.get("message", ""))
        )
        return {
            "skill_name": skill_name,
            "reason": str(args.get("reason", "")),
            "result": result
        }
