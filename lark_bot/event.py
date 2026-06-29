import hashlib
import json
import typing as t

from .decrypt import AESCipher
from .utils import dict_2_obj


class Event(object):
    callback_handler = None

    def __init__(self, dict_data, token, encrypt_key, headers, body: bytes):
        header = dict_data.get("header")
        event = dict_data.get("event")
        if header is None or event is None:
            raise InvalidEventException("request is not callback event(v2)")
        self.header = dict_2_obj(header)
        self.event = dict_2_obj(event)
        self._validate(token, encrypt_key, headers, body)

    def _validate(self, token, encrypt_key, headers, body: bytes):
        if self.header.token != token:
            raise InvalidEventException("invalid token")
        timestamp = headers.get("X-Lark-Request-Timestamp")
        nonce = headers.get("X-Lark-Request-Nonce")
        signature = headers.get("X-Lark-Signature")
        if not timestamp or not nonce or not signature:
            raise InvalidEventException("missing signature headers")
        bytes_b1 = (timestamp + nonce + encrypt_key).encode("utf-8")
        bytes_b = bytes_b1 + body
        h = hashlib.sha256(bytes_b)
        if signature != h.hexdigest():
            raise InvalidEventException("invalid signature in event")


class MessageReceiveEvent(Event):
    @staticmethod
    def event_type():
        return "im.message.receive_v1"


class UrlVerificationEvent(Event):
    def __init__(self, dict_data):
        self.event = dict_2_obj(dict_data)

    @staticmethod
    def event_type():
        return "url_verification"


class EventManager(object):
    event_callback_map = dict()
    event_type_map = dict()
    _event_list = [MessageReceiveEvent, UrlVerificationEvent]

    def __init__(self):
        for event in EventManager._event_list:
            EventManager.event_type_map[event.event_type()] = event

    def register(self, event_type: str) -> t.Callable:
        def decorator(f: t.Callable) -> t.Callable:
            self.register_handler_with_event_type(event_type=event_type, handler=f)
            return f

        return decorator

    @staticmethod
    def register_handler_with_event_type(event_type, handler):
        EventManager.event_callback_map[event_type] = handler

    @staticmethod
    def get_handler_with_event(token, encrypt_key, body: bytes, headers):
        dict_data = json.loads(body)
        dict_data = EventManager._decrypt_data(encrypt_key, dict_data)
        callback_type = dict_data.get("type")
        if callback_type == "url_verification":
            event = UrlVerificationEvent(dict_data)
            return EventManager.event_callback_map.get(event.event_type()), event

        schema = dict_data.get("schema")
        if schema is None:
            raise InvalidEventException("request is not callback event(v2)")

        event_type = dict_data.get("header").get("event_type")
        event_class = EventManager.event_type_map.get(event_type)
        if event_class is None:
            raise InvalidEventException(f"unsupported event type: {event_type}")
        event = event_class(dict_data, token, encrypt_key, headers, body)
        return EventManager.event_callback_map.get(event_type), event

    @staticmethod
    def _decrypt_data(encrypt_key, data):
        encrypt_data = data.get("encrypt")
        if encrypt_key == "" and encrypt_data is None:
            return data
        if encrypt_key == "":
            raise Exception("ENCRYPT_KEY is necessary")
        cipher = AESCipher(encrypt_key)
        return json.loads(cipher.decrypt_string(encrypt_data))


class InvalidEventException(Exception):
    def __init__(self, error_info):
        self.error_info = error_info

    def __str__(self) -> str:
        return "Invalid event: {}".format(self.error_info)

    __repr__ = __str__
