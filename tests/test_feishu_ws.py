from dataclasses import dataclass

from lark_bot.ws import FeishuWebSocketService


@dataclass
class FakeFeishuConfig:
    app_id: str = "cli_test"
    app_secret: str = "secret"


@dataclass
class FakeTenantConfig:
    tenant_id: str
    feishu: FakeFeishuConfig


class BrokenRegistry:
    def list_configs(self):
        return [FakeTenantConfig("broken", FakeFeishuConfig())]

    def get(self, tenant_id):
        raise ValueError("missing key")


def test_websocket_start_skips_tenant_runtime_init_failure():
    service = FeishuWebSocketService(BrokenRegistry())

    service.start()

    assert service._workers == []
