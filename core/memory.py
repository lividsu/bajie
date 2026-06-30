"""Tenant memory store for reusable task context."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import time


@dataclass
class MemoryEntry:
    slug: str
    title: str
    description: str
    path: Path
    content: str = ""


class MemoryStore:
    def __init__(self, memory_dir: Path):
        self.memory_dir = memory_dir
        self.index_path = memory_dir / "MEMORY.md"

    def ensure_exists(self) -> None:
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        if not self.index_path.exists():
            self.index_path.write_text("# Memory\n\n", encoding="utf-8")

    def search(self, query: str, *, skills: list[str] | None = None, limit: int = 5) -> list[MemoryEntry]:
        if not self.index_path.exists():
            return []
        raw_index = self.index_path.read_text(encoding="utf-8")
        query_terms = self._terms(query)
        entries: list[tuple[int, MemoryEntry]] = []
        for line in raw_index.splitlines():
            entry = self._parse_index_line(line)
            if not entry:
                continue
            score = self._score(entry.title + " " + entry.description, query_terms)
            if skills and entry.path.exists():
                content = entry.path.read_text(encoding="utf-8")
                if any(skill in content for skill in skills):
                    score += 2
                entry.content = content
            if score > 0:
                entries.append((score, entry))
        entries.sort(key=lambda item: item[0], reverse=True)
        return [entry for _, entry in entries[:limit]]

    def snippets_for(self, query: str, *, skills: list[str] | None = None, limit: int = 5) -> list[str]:
        snippets = []
        for entry in self.search(query, skills=skills, limit=limit):
            content = entry.content
            if not content and entry.path.exists():
                content = entry.path.read_text(encoding="utf-8")
            snippets.append(content.strip()[:1200] if content else entry.description)
        return snippets

    def write_learning(
        self,
        *,
        title: str,
        learned: str,
        how_to_apply: str,
        related_skills: list[str],
        source_chat: str,
    ) -> Path:
        self.ensure_exists()
        slug = self._slugify(title)
        path = self.memory_dir / f"{slug}.md"
        created = time.strftime("%Y-%m-%d")
        body = (
            "---\n"
            f"name: {slug}\n"
            f"description: {learned}\n"
            "metadata:\n"
            "  type: feedback\n"
            f"  created: {created}\n"
            f"  related_skills: [{', '.join(related_skills)}]\n"
            f"  source_chat: {source_chat}\n"
            "---\n\n"
            f"{learned}\n\n"
            f"**How to apply:** {how_to_apply}\n"
        )
        path.write_text(body, encoding="utf-8")
        index_line = f"- [{title}]({path.name}) - {learned}\n"
        current = self.index_path.read_text(encoding="utf-8")
        if path.name not in current:
            self.index_path.write_text(current.rstrip() + "\n" + index_line, encoding="utf-8")
        return path

    def _parse_index_line(self, line: str) -> MemoryEntry | None:
        match = re.match(r"\s*-\s*\[(?P<title>[^\]]+)\]\((?P<path>[^)]+)\)\s*[-:：]?\s*(?P<desc>.*)", line)
        if not match:
            return None
        relative = match.group("path").strip()
        path = self.memory_dir / relative
        return MemoryEntry(
            slug=Path(relative).stem,
            title=match.group("title").strip(),
            description=match.group("desc").strip(),
            path=path,
        )

    def _terms(self, text: str) -> set[str]:
        lowered = (text or "").lower()
        terms = set(re.findall(r"[a-z0-9_+-]{2,}", lowered))
        terms.update(re.findall(r"[\u4e00-\u9fff]{2,}", lowered))
        return terms

    def _score(self, text: str, terms: set[str]) -> int:
        haystack = text.lower()
        return sum(1 for term in terms if term in haystack)

    def _slugify(self, title: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", title.strip().lower()).strip("-")
        return slug or f"memory-{int(time.time())}"
