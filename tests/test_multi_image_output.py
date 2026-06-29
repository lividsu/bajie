import re
import sys
import threading
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

from llm.processor import MessageProcessor
from skills.reflection.scripts.main import execute as execute_reflection


def _processor(max_output_images=4):
    processor = object.__new__(MessageProcessor)
    processor.tenant_config = SimpleNamespace(
        limits=SimpleNamespace(max_output_images=max_output_images)
    )
    return processor


def test_requested_output_count_is_capped_by_tenant_limit():
    processor = _processor(max_output_images=4)

    assert processor._requested_output_count("生成3张猫咪海报") == 3
    assert processor._requested_output_count("生成10张猫咪海报") == 4
    assert processor._requested_output_count("生成猫咪海报") == 1


def test_parallel_output_runner_aggregates_single_task_results():
    processor = _processor(max_output_images=4)
    lock = threading.Lock()
    task_messages = []

    def fake_run_tool_loop(**kwargs):
        assert kwargs["allow_parallel_outputs"] is False
        with lock:
            task_messages.append(kwargs["message"])
        index = int(re.search(r"第 (\d+)/3", kwargs["message"]).group(1))
        return {
            "text": f"ok {index}",
            "image_path": f"/tmp/generated_{index}.png",
        }

    processor._run_tool_loop = fake_run_tool_loop

    result = processor._run_parallel_output_tasks(
        output_count=3,
        message="生成3张猫咪海报",
        chat_id="chat",
        has_images=False,
        image_paths=None,
        has_files=False,
        file_paths=None,
        file_exts=None,
        use_pro=False,
        current_attempt=0,
        original_prompt="生成3张猫咪海报",
    )

    assert len(task_messages) == 3
    assert all("请只生成 1 张图片" in message for message in task_messages)
    assert result["image_path"] == "/tmp/generated_1.png"
    assert result["image_paths"] == [
        "/tmp/generated_1.png",
        "/tmp/generated_2.png",
        "/tmp/generated_3.png",
    ]
    assert result["needs_reflection"] is False


def test_tool_loop_dispatches_parallel_outputs_before_skill_execution():
    processor = _processor(max_output_images=4)
    processor.determine_skill = lambda **_kwargs: "image_gen"

    def fake_parallel(**kwargs):
        return {
            "text": "parallel",
            "image_path": "/tmp/one.png",
            "image_paths": ["/tmp/one.png", "/tmp/two.png"],
        }

    processor._run_parallel_output_tasks = fake_parallel

    result = MessageProcessor._run_tool_loop(
        processor,
        message="生成2张猫咪海报",
        chat_id="chat",
        has_images=False,
        image_paths=None,
        has_files=False,
        file_paths=None,
        file_exts=None,
        use_pro=False,
        current_attempt=0,
        original_prompt="生成2张猫咪海报",
    )

    assert result["image_paths"] == ["/tmp/one.png", "/tmp/two.png"]


def test_obvious_draw_request_dispatches_directly_to_image_gen():
    processor = _processor(max_output_images=4)
    processor.message_api_client = None

    class FakeSkillsLoader:
        def execute_skill(self, **kwargs):
            assert kwargs["name"] == "image_gen"
            assert kwargs["message"] == "画个西瓜"
            return {
                "text": "image generated",
                "image_path": "/tmp/watermelon.png",
            }

    processor.skills_loader = FakeSkillsLoader()
    processor._plan_next_action = lambda **_kwargs: (_ for _ in ()).throw(
        AssertionError("planner should not run for direct image requests")
    )

    result = MessageProcessor._run_tool_loop(
        processor,
        message="画个西瓜",
        chat_id="chat",
        has_images=False,
        image_paths=None,
        has_files=False,
        file_paths=None,
        file_exts=None,
        use_pro=False,
        current_attempt=0,
        original_prompt="画个西瓜",
    )

    assert result["image_path"] == "/tmp/watermelon.png"
    assert result["tool_trace"][0]["skill"] == "image_gen"


def test_canvas_recompose_image_request_dispatches_directly_to_image_gen():
    processor = _processor(max_output_images=4)

    class FailIfClassifierRuns:
        def get_ai_response(self, *_args, **_kwargs):
            raise AssertionError("classifier should not run for canvas recompose image requests")

    processor.chat_handler = FailIfClassifierRuns()

    skill = MessageProcessor.determine_skill(
        processor,
        message="重构这张图，改成适合手机端的4：3的比例",
        has_images=True,
        num_images=1,
    )

    assert skill == "image_gen"


def test_retry_attempt_forces_image_gen_even_if_classifier_says_general():
    processor = _processor(max_output_images=4)
    processor.message_api_client = None
    processor.determine_skill = lambda **_kwargs: "general"

    class FakeSkillsLoader:
        def execute_skill(self, **kwargs):
            assert kwargs["name"] == "image_gen"
            assert kwargs["message"] == "more detailed watermelon prompt"
            return {
                "text": "retry generated",
                "image_path": "/tmp/watermelon_retry.png",
            }

    processor.skills_loader = FakeSkillsLoader()

    result = MessageProcessor._run_tool_loop(
        processor,
        message="more detailed watermelon prompt",
        chat_id="chat",
        has_images=False,
        image_paths=None,
        has_files=False,
        file_paths=None,
        file_exts=None,
        use_pro=False,
        current_attempt=1,
        original_prompt="画个西瓜",
    )

    assert result["image_path"] == "/tmp/watermelon_retry.png"
    assert result["tool_trace"][0]["skill"] == "image_gen"


def test_reflection_score_at_threshold_does_not_retry_and_reports_pass():
    class FakeChatHandler:
        def reflect_on_generated_image(self, **_kwargs):
            return {
                "score": 8,
                "is_satisfactory": False,
                "analysis": "整体不错，但有轻微问题。",
                "strengths": ["主体明确"],
                "issues": ["局部细节轻微生硬"],
                "improved_prompt": "improved prompt",
            }

    processor = SimpleNamespace(
        chat_handler=FakeChatHandler(),
        test_mode=False,
        max_retry_attempts=2,
        min_satisfactory_score=7,
        create_optimization_message=lambda **_kwargs: "optimization",
    )

    result = execute_reflection(
        message="",
        chat_id="chat",
        processor=processor,
        reflection_context={
            "generated_image_path": "/tmp/watermelon.png",
            "original_prompt": "画个西瓜",
            "attempt": 0,
        },
    )

    assert result["should_retry"] is False
    assert "自检 8/10" in result["text"]
    assert "不需要重画" in result["text"]


def test_reflection_retry_reports_improved_prompt_to_user():
    class FakeChatHandler:
        def reflect_on_generated_image(self, **_kwargs):
            return {
                "score": 5,
                "is_satisfactory": False,
                "analysis": "主体没有清楚呈现用户要的西瓜。",
                "strengths": ["色彩还算鲜明"],
                "issues": ["主体不明确"],
                "improved_prompt": "A clear illustration of a whole watermelon and one sliced watermelon, clean composition.",
            }

    processor = SimpleNamespace(
        chat_handler=FakeChatHandler(),
        test_mode=False,
        max_retry_attempts=2,
        min_satisfactory_score=7,
        create_optimization_message=lambda improved_prompt, attempt, original_prompt: (
            f"[优化重试] attempt={attempt} | original={original_prompt} | improved={improved_prompt}"
        ),
    )

    result = execute_reflection(
        message="",
        chat_id="chat",
        processor=processor,
        reflection_context={
            "generated_image_path": "/tmp/watermelon.png",
            "original_prompt": "画个西瓜",
            "attempt": 0,
        },
    )

    assert result["should_retry"] is True
    assert "新的 prompt" in result["text"]
    assert "A clear illustration of a whole watermelon" in result["text"]
    assert "pro模式" not in result["optimization_message"]
