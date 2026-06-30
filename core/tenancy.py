"""Tenant configuration and runtime context."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import time

import yaml

from core.bounded_cache import PersistentTTLBoundedSet
from core.conversation_state import ConversationStateManager
from core.skills_loader import SkillsLoader


@dataclass(frozen=True)
class FeishuConfig:
    app_id: str | None = None
    app_secret: str | None = None
    verification_token: str | None = None
    encrypt_key: str | None = None
    lark_host: str | None = "https://open.feishu.cn"
    bot_name: str = "八戒-Dev"
    bot_open_id: str = ""
    processing_emoji: str = "OnIt"
    done_emoji: str = "DONE"
    failed_emoji: str = ""


@dataclass(frozen=True)
class LlmConfig:
    provider: str = "gemini"
    openai_api_key: str | None = None
    deepseek_api_key: str | None = None
    gemini_api_key: str | None = None
    google_api_key: str | None = None
    fast_model: str = "gemini-3-flash-preview"
    judge_model: str = "gemini-3-flash-preview"
    image_gen_model: str = "gemini-3.1-flash-image-preview"
    image_gen_pro_model: str = "gemini-3-pro-image-preview"
    image_understand_model: str = "gemini-3-pro-preview"


@dataclass(frozen=True)
class TenantLimits:
    max_images: int = 4
    max_output_images: int = 4
    file_retention_days: int = 15
    processed_event_ttl_seconds: int = 86400
    processed_event_cache_size: int = 10000
    stale_message_grace_seconds: int = 300
    max_tool_iterations: int = 5
    ai_test_mode: bool = False


@dataclass(frozen=True)
class TenantFeatures:
    compile_and_execute: bool = True
    memory_system: bool = True
    clarification_loop: bool = True


@dataclass(frozen=True)
class TenantConfig:
    tenant_id: str
    name: str
    root_dir: Path
    feishu: FeishuConfig
    llm: LlmConfig
    limits: TenantLimits = field(default_factory=TenantLimits)
    features: TenantFeatures = field(default_factory=TenantFeatures)

    @property
    def skills_dir(self) -> Path:
        return self.root_dir / "skills"

    @property
    def cache_dir(self) -> Path:
        return self.root_dir / "cache"

    @property
    def image_cache_dir(self) -> Path:
        return self.cache_dir / "images"

    @property
    def file_cache_dir(self) -> Path:
        return self.cache_dir / "files"

    @property
    def generated_images_dir(self) -> Path:
        return self.cache_dir / "generated_images"

    @property
    def memory_dir(self) -> Path:
        return self.root_dir / "memory"


@dataclass
class TenantContext:
    config: TenantConfig
    message_api_client: MessageApiClient
    message_processor: MessageProcessor
    processed_events: PersistentTTLBoundedSet
    conversation_state_manager: ConversationStateManager
    started_at: float = field(default_factory=time.time)

    @property
    def tenant_id(self) -> str:
        return self.config.tenant_id


class TenantRegistry:
    def __init__(self, tenants_root: Path, default_tenant_id: str = "default"):
        self.tenants_root = tenants_root
        self.default_tenant_id = default_tenant_id
        self._configs: dict[str, TenantConfig] = {}
        self._contexts: dict[str, TenantContext] = {}
        self._load_configs()

    def get(self, tenant_id: str | None = None) -> TenantContext:
        resolved_id = tenant_id or self.default_tenant_id
        if resolved_id not in self._configs:
            raise KeyError(f"Unknown tenant: {resolved_id}")
        if resolved_id not in self._contexts:
            self._contexts[resolved_id] = self._build_context(self._configs[resolved_id])
        return self._contexts[resolved_id]

    def list_configs(self) -> list[TenantConfig]:
        return list(self._configs.values())

    def _load_configs(self):
        self._configs.clear()
        if self.tenants_root.exists():
            for tenant_dir in self.tenants_root.iterdir():
                if not tenant_dir.is_dir():
                    continue
                config_path = self._tenant_config_path(tenant_dir)
                if config_path.exists():
                    tenant_config = self._load_yaml_config(config_path, tenant_dir)
                    self._configs[tenant_config.tenant_id] = tenant_config

    @staticmethod
    def _tenant_config_path(tenant_dir: Path) -> Path:
        for name in ("config.yaml", "config.yml"):
            path = tenant_dir / name
            if path.exists():
                return path
        return tenant_dir / "config.yaml"

    def _build_context(self, tenant_config: TenantConfig) -> TenantContext:
        from lark_bot.api import MessageApiClient
        from llm.chat_client import ChatHandler
        from llm.processor import MessageProcessor

        tenant_config.image_cache_dir.mkdir(parents=True, exist_ok=True)
        tenant_config.file_cache_dir.mkdir(parents=True, exist_ok=True)
        tenant_config.generated_images_dir.mkdir(parents=True, exist_ok=True)
        tenant_config.memory_dir.mkdir(parents=True, exist_ok=True)

        skills_loader = SkillsLoader(
            workspace=Path.cwd(),
            tenant_skills_dir=tenant_config.skills_dir,
            common_skills_dir=Path.cwd() / "skills",
        )
        chat_handler = ChatHandler.from_tenant_config(tenant_config.llm)
        message_processor = MessageProcessor(
            tenant_config=tenant_config,
            skills_loader=skills_loader,
            chat_handler=chat_handler,
            generated_images_dir=tenant_config.generated_images_dir,
        )
        conversation_state_manager = ConversationStateManager()
        message_processor.conversation_state_manager = conversation_state_manager
        message_api_client = MessageApiClient(
            tenant_config.feishu.app_id,
            tenant_config.feishu.app_secret,
            tenant_config.feishu.lark_host,
            image_cache_dir=tenant_config.image_cache_dir,
            file_cache_dir=tenant_config.file_cache_dir,
        )
        message_processor.message_api_client = message_api_client
        processed_events = PersistentTTLBoundedSet(
            db_path=tenant_config.cache_dir / "processed_events.sqlite3",
            max_size=tenant_config.limits.processed_event_cache_size * 2,
            ttl_seconds=tenant_config.limits.processed_event_ttl_seconds,
        )
        return TenantContext(
            config=tenant_config,
            message_api_client=message_api_client,
            message_processor=message_processor,
            processed_events=processed_events,
            conversation_state_manager=conversation_state_manager,
        )

    def _load_yaml_config(self, config_path: Path, tenant_dir: Path) -> TenantConfig:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"{config_path} must contain a YAML mapping")
        tenant_id = str(raw.get("id") or tenant_dir.name)
        return TenantConfig(
            tenant_id=tenant_id,
            name=str(raw.get("name") or tenant_id),
            root_dir=tenant_dir,
            feishu=FeishuConfig(**(raw.get("feishu", {}) or {})),
            llm=LlmConfig(**(raw.get("llm", {}) or {})),
            limits=TenantLimits(**raw.get("limits", {})),
            features=TenantFeatures(**raw.get("features", {})),
        )
