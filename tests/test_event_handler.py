from types import SimpleNamespace

from core.event_handler import _message_create_time_seconds


def test_message_create_time_seconds_accepts_seconds():
    message = SimpleNamespace(create_time="1782738663")

    assert _message_create_time_seconds(message) == 1782738663


def test_message_create_time_seconds_accepts_milliseconds():
    message = SimpleNamespace(create_time="1782738663000")

    assert _message_create_time_seconds(message) == 1782738663


def test_message_create_time_seconds_ignores_missing_or_invalid_values():
    assert _message_create_time_seconds(SimpleNamespace()) is None
    assert _message_create_time_seconds(SimpleNamespace(create_time="not-a-time")) is None
