import json
import sys
import types as py_types
from types import SimpleNamespace

google_module = sys.modules.setdefault("google", py_types.ModuleType("google"))
genai_module = py_types.ModuleType("google.genai")
genai_module.Client = object
genai_module.types = SimpleNamespace(
    Part=object,
    GenerateContentConfig=object,
    ImageConfig=object,
)
sys.modules["google.genai"] = genai_module
setattr(google_module, "genai", genai_module)

from core.conversation_state import ConversationStateManager
from core.executor import ExecutionRunner
from core.intent import IntentParser
from llm.compiler import ExecutionCompiler
from llm.processor import MessageProcessor


class FakeSkillsLoader:
    def __init__(self):
        self.calls = []

    def build_skills_summary(self):
        return "<skills><skill><name>general</name></skill></skills>"

    def list_skills(self, *args, **kwargs):
        return [{"name": "general"}]

    def execute_skill(self, **kwargs):
        self.calls.append(kwargs)
        return {"text": f"done: {kwargs['message']}"}


class FakeChatHandler:
    def get_ai_response(self, *args, **kwargs):
        return json.dumps(
            {
                "execution_mode": "parallel",
                "summary": "two tasks",
                "tasks": [
                    {
                        "id": "task_001",
                        "item_id": "item_001",
                        "skill": "general",
                        "mode": "chat",
                        "message": "handle A",
                        "image_indices": [],
                        "file_indices": [],
                        "context": {},
                    },
                    {
                        "id": "task_002",
                        "item_id": "item_002",
                        "skill": "general",
                        "mode": "chat",
                        "message": "handle B",
                        "image_indices": [],
                        "file_indices": [],
                        "context": {},
                    },
                ],
            }
        )


def _processor():
    processor = object.__new__(MessageProcessor)
    processor.tenant_config = SimpleNamespace(
        features=SimpleNamespace(
            compile_and_execute=True,
            memory_system=False,
            clarification_loop=True,
        ),
        limits=SimpleNamespace(max_output_images=4),
    )
    processor.skills_loader = FakeSkillsLoader()
    processor.chat_handler = FakeChatHandler()
    processor.intent_parser = IntentParser()
    processor.conversation_state_manager = ConversationStateManager()
    processor.execution_compiler = ExecutionCompiler(processor.chat_handler, processor.skills_loader)
    processor.execution_runner = ExecutionRunner(processor)
    processor.message_api_client = None
    processor.max_tool_iterations = 5
    processor.determine_skill = lambda **_kwargs: "general"
    processor._requested_output_count = lambda _message: 1
    return processor


def test_intent_parser_detects_multi_image_batch_edit():
    signal = IntentParser().parse(
        "把这两张图都改成白底黑图",
        primary_skill="image_gen",
        has_images=True,
        num_images=2,
    )

    assert signal.action == "decompose"
    assert [item.reference_indices for item in signal.items] == [[0], [1]]


def test_intent_parser_keeps_single_clear_request_on_fast_path():
    signal = IntentParser().parse(
        "画一个西瓜",
        primary_skill="image_gen",
    )

    assert signal.action == "pass_through"


def test_compile_and_execute_path_runs_compiled_tasks():
    processor = _processor()

    result = MessageProcessor._run_tool_loop(
        processor,
        message="分别处理 A 和 B",
        chat_id="chat",
        has_images=False,
        image_paths=None,
        has_files=False,
        file_paths=None,
        file_exts=None,
        use_pro=False,
        current_attempt=0,
        original_prompt="分别处理 A 和 B",
    )

    assert result["compiled_plan"]["task_count"] == 2
    assert [call["message"] for call in processor.skills_loader.calls] == ["handle A", "handle B"]


def test_ambiguous_count_creates_pending_clarification_then_resolves():
    manager = ConversationStateManager()
    parser = IntentParser()
    first = parser.parse("画两个西瓜", primary_skill="image_gen")
    request = manager.create_subject_count_clarification("chat", first.items[0])

    assert manager.get_pending("chat") == request

    resolved = manager.resolve_pending("chat", "两张独立的")

    assert resolved is not None
    assert resolved.ambiguity_flags == []
    assert "多张独立结果" in resolved.description
    assert manager.get_pending("chat") is None
