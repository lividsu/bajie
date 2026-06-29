from pathlib import Path

from config import config
from lark_bot.event import EventManager
from core.tenancy import TenantRegistry


event_manager = EventManager()
tenant_registry = TenantRegistry(
    tenants_root=Path(config.TENANTS_CONFIG_DIR),
    default_tenant_id=config.DEFAULT_TENANT_ID,
)
