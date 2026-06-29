"""Skills loader for agent capabilities."""

import json
import os
import re
import shutil
import ast
import importlib.util
import sys
from pathlib import Path
from typing import Any

# Default builtin skills directory (relative to this file)
BUILTIN_SKILLS_DIR = Path(__file__).parent.parent / "skills"


class SkillsLoader:
    """
    Loader for agent skills.

    Skills are markdown files (SKILL.md) that teach the agent how to use
    specific tools or perform certain tasks.
    """

    def __init__(
        self,
        workspace: Path,
        builtin_skills_dir: Path | None = None,
        tenant_skills_dir: Path | None = None,
        common_skills_dir: Path | None = None,
    ):
        self.workspace = workspace
        self.tenant_skills = tenant_skills_dir or workspace / "skills"
        self.common_skills = common_skills_dir or workspace / "skills"
        self.builtin_skills = builtin_skills_dir or BUILTIN_SKILLS_DIR
        self._metadata_cache: dict[str, dict[str, Any] | None] = {}

    def list_skills(self, filter_unavailable: bool = True, filter_unexecutable: bool = True) -> list[dict[str, str]]:
        """
        List all available skills.

        Args:
            filter_unavailable: If True, filter out skills with unmet requirements.
            filter_unexecutable: If True, filter out skills without scripts/main.py.

        Returns:
            List of skill info dicts with 'name', 'path', 'source'.
        """
        skills = []

        # Tenant/workspace skills (highest priority)
        if self.tenant_skills.exists():
            for skill_dir in self.tenant_skills.iterdir():
                if skill_dir.is_dir():
                    skill_file = skill_dir / "SKILL.md"
                    if skill_file.exists():
                        skills.append({"name": skill_dir.name, "path": str(skill_file), "source": "tenant"})

        # Common workspace skills
        if self.common_skills and self.common_skills.exists():
            for skill_dir in self.common_skills.iterdir():
                if skill_dir.is_dir():
                    skill_file = skill_dir / "SKILL.md"
                    if skill_file.exists() and not any(s["name"] == skill_dir.name for s in skills):
                        skills.append({"name": skill_dir.name, "path": str(skill_file), "source": "common"})

        # Built-in skills
        if self.builtin_skills and self.builtin_skills.exists():
            for skill_dir in self.builtin_skills.iterdir():
                if skill_dir.is_dir():
                    skill_file = skill_dir / "SKILL.md"
                    if skill_file.exists() and not any(s["name"] == skill_dir.name for s in skills):
                        skills.append({"name": skill_dir.name, "path": str(skill_file), "source": "builtin"})

        if filter_unexecutable:
            skills = [s for s in skills if self._has_executable_entry(s["name"])]

        if filter_unavailable:
            skills = [s for s in skills if self._check_requirements(self._get_skill_meta(s["name"]))]
        return skills

    def _has_executable_entry(self, name: str) -> bool:
        tenant_main = self.tenant_skills / name / "scripts" / "main.py"
        if tenant_main.exists():
            return self._main_defines_execute(tenant_main)
        common_main = self.common_skills / name / "scripts" / "main.py"
        if common_main.exists():
            return self._main_defines_execute(common_main)
        if self.builtin_skills:
            builtin_main = self.builtin_skills / name / "scripts" / "main.py"
            return self._main_defines_execute(builtin_main)
        return False

    def _main_defines_execute(self, module_path: Path) -> bool:
        if not module_path.exists():
            return False
        try:
            tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
        except (OSError, SyntaxError, UnicodeDecodeError):
            return False
        return any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "execute"
            for node in tree.body
        )

    def load_skill(self, name: str) -> str | None:
        """
        Load a skill by name.

        Args:
            name: Skill name (directory name).

        Returns:
            Skill content or None if not found.
        """
        # Check workspace first
        tenant_skill = self.tenant_skills / name / "SKILL.md"
        if tenant_skill.exists():
            return tenant_skill.read_text(encoding="utf-8")

        # Check common workspace skills
        common_skill = self.common_skills / name / "SKILL.md"
        if common_skill.exists():
            return common_skill.read_text(encoding="utf-8")

        # Check built-in
        if self.builtin_skills:
            builtin_skill = self.builtin_skills / name / "SKILL.md"
            if builtin_skill.exists():
                return builtin_skill.read_text(encoding="utf-8")

        return None

    def load_skills_for_context(self, skill_names: list[str]) -> str:
        """
        Load specific skills for inclusion in agent context.

        Args:
            skill_names: List of skill names to load.

        Returns:
            Formatted skills content.
        """
        parts = []
        for name in skill_names:
            content = self.load_skill(name)
            if content:
                content = self._strip_frontmatter(content)
                parts.append(f"### Skill: {name}\n\n{content}")

        return "\n\n---\n\n".join(parts) if parts else ""

    def build_skills_summary(self) -> str:
        """
        Build a summary of all skills (name, description, path, availability).

        This is used for progressive loading - the agent can read the full
        skill content using read_file when needed.

        Returns:
            XML-formatted skills summary.
        """
        all_skills = self.list_skills(filter_unavailable=False)
        if not all_skills:
            return ""

        def escape_xml(s: str) -> str:
            return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        lines = ["<skills>"]
        for s in all_skills:
            name = escape_xml(s["name"])
            path = s["path"]
            desc = escape_xml(self._get_skill_description(s["name"]))
            skill_meta = self._get_skill_meta(s["name"])
            available = self._check_requirements(skill_meta)

            lines.append(f"  <skill available=\"{str(available).lower()}\">")
            lines.append(f"    <name>{name}</name>")
            lines.append(f"    <description>{desc}</description>")
            lines.append(f"    <location>{path}</location>")

            # Show missing requirements for unavailable skills
            if not available:
                missing = self._get_missing_requirements(skill_meta)
                if missing:
                    lines.append(f"    <requires>{escape_xml(missing)}</requires>")

            lines.append("  </skill>")
        lines.append("</skills>")

        return "\n".join(lines)

    def _get_missing_requirements(self, skill_meta: dict) -> str:
        """Get a description of missing requirements."""
        missing = []
        requires = skill_meta.get("requires", {})
        for b in requires.get("bins", []):
            if not shutil.which(b):
                missing.append(f"CLI: {b}")
        for env in requires.get("env", []):
            if not os.environ.get(env):
                missing.append(f"ENV: {env}")
        return ", ".join(missing)

    def _get_skill_description(self, name: str) -> str:
        """Get the description of a skill from its frontmatter."""
        meta = self.get_skill_metadata(name)
        if meta and meta.get("description"):
            return meta["description"]
        return name

    def _strip_frontmatter(self, content: str) -> str:
        """Remove YAML frontmatter from markdown content."""
        if content.startswith("---"):
            match = re.match(r"^---\n.*?\n---\n", content, re.DOTALL)
            if match:
                return content[match.end():].strip()
        return content

    def _parse_nanobot_metadata(self, raw: str) -> dict:
        """Parse skill metadata JSON from frontmatter (supports nanobot and openclaw keys)."""
        try:
            data = json.loads(raw)
            return data.get("nanobot", data.get("openclaw", {})) if isinstance(data, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    def _check_requirements(self, skill_meta: dict) -> bool:
        """Check if skill requirements are met (bins, env vars)."""
        requires = skill_meta.get("requires", {})
        for b in requires.get("bins", []):
            if not shutil.which(b):
                return False
        for env in requires.get("env", []):
            if not os.environ.get(env):
                return False
        return True

    def _get_skill_meta(self, name: str) -> dict:
        """Get nanobot metadata for a skill (cached in frontmatter)."""
        meta = self.get_skill_metadata(name) or {}
        return self._parse_nanobot_metadata(meta.get("metadata", ""))

    def get_always_skills(self) -> list[str]:
        """Get skills marked as always=true that meet requirements."""
        result = []
        for s in self.list_skills(filter_unavailable=True):
            meta = self.get_skill_metadata(s["name"]) or {}
            skill_meta = self._parse_nanobot_metadata(meta.get("metadata", ""))
            if skill_meta.get("always") or meta.get("always"):
                result.append(s["name"])
        return result

    def load_skill_module(self, name: str):
        """
        Dynamically load the main.py module for a given skill.

        Args:
            name: Skill name.

        Returns:
            The loaded module or None if not found.
        """
        module_path = self._get_skill_root(name) / "scripts" / "main.py"
        if not module_path.exists():
            module_path = None

        if not module_path:
            return None

        module_name = f"skills_{abs(hash(str(module_path.parent.parent.resolve())))}.{name}.scripts.main"
        spec = importlib.util.spec_from_file_location(module_name, str(module_path))
        if spec and spec.loader:
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            return module

        return None

    def execute_skill(self, name: str, *args, **kwargs):
        """
        Execute the 'execute' function of a given skill.

        Args:
            name: Skill name.
            *args, **kwargs: Arguments to pass to the skill's execute function.

        Returns:
            The result of the skill's execute function.
        """
        module = self.load_skill_module(name)
        if module and hasattr(module, "execute"):
            return module.execute(*args, **kwargs)
        raise NotImplementedError(f"Skill '{name}' does not implement an 'execute' function in scripts/main.py")

    def get_skill_metadata(self, name: str) -> dict | None:
        """
        Get metadata from a skill's frontmatter.

        Args:
            name: Skill name.

        Returns:
            Metadata dict or None.
        """
        if name in self._metadata_cache:
            return self._metadata_cache[name]
        content = self.load_skill(name)
        if not content:
            self._metadata_cache[name] = None
            return None
        metadata = self._extract_frontmatter(content)
        self._metadata_cache[name] = metadata
        return metadata

    def validate_skills(self) -> dict[str, list[dict[str, str]]]:
        report = {"errors": [], "warnings": []}
        all_skills = self.list_skills(filter_unavailable=False, filter_unexecutable=False)
        for skill in all_skills:
            name = skill["name"]
            metadata = self.get_skill_metadata(name) or {}
            if not metadata:
                report["errors"].append({"skill": name, "reason": "SKILL.md 缺少 frontmatter"})
                continue
            meta_name = str(metadata.get("name", "")).strip()
            if not meta_name:
                report["errors"].append({"skill": name, "reason": "frontmatter 缺少 name"})
            elif meta_name != name:
                report["errors"].append({"skill": name, "reason": f"name 与目录名不一致: {meta_name}"})
            if not str(metadata.get("description", "")).strip():
                report["warnings"].append({"skill": name, "reason": "frontmatter 缺少 description"})
            if not self._has_executable_entry(name):
                report["errors"].append({"skill": name, "reason": "缺少 scripts/main.py"})
            skill_meta = self._get_skill_meta(name)
            if not self._check_requirements(skill_meta):
                missing = self._get_missing_requirements(skill_meta)
                report["warnings"].append({"skill": name, "reason": f"依赖未满足: {missing}"})
        return report

    def _extract_frontmatter(self, content: str) -> dict[str, Any] | None:
        if not content.startswith("---"):
            return None
        match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
        if not match:
            return None
        return self._parse_frontmatter_lines(match.group(1).split("\n"))

    def _parse_frontmatter_lines(self, lines: list[str]) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        current_key: str | None = None
        for raw in lines:
            line = raw.rstrip()
            if not line.strip():
                continue
            if line.lstrip().startswith("#"):
                continue
            if line.startswith("  - ") and current_key:
                current = metadata.get(current_key)
                if not isinstance(current, list):
                    current = [] if current in (None, "") else [current]
                current.append(line[4:].strip().strip('"\''))
                metadata[current_key] = current
                continue
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()
            current_key = key
            if value == "":
                metadata[key] = []
                continue
            parsed_value: Any = value.strip('"\'')
            lowered = str(parsed_value).lower()
            if lowered == "true":
                parsed_value = True
            elif lowered == "false":
                parsed_value = False
            metadata[key] = parsed_value
        return metadata

    def _get_skill_root(self, name: str) -> Path:
        tenant_root = self.tenant_skills / name
        if tenant_root.exists():
            return tenant_root
        common_root = self.common_skills / name
        if common_root.exists():
            return common_root
        if self.builtin_skills:
            builtin_root = self.builtin_skills / name
            if builtin_root.exists():
                return builtin_root
        return tenant_root
