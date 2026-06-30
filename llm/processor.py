# message_processor.py
from typing import Tuple, List, Union, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
import time
import json
import re
import uuid
from pathlib import Path
from .chat_client import ChatHandler
from .compiler import ExecutionCompiler
from core.skills_loader import SkillsLoader
from core.conversation_state import ConversationStateManager
from core.executor import ExecutionRunner
from core.intent import IntentSignal, IntentParser
from core.memory import MemoryStore
from core.tools import ToolRegistry, ExecuteSkillTool, FeishuDocsTool
from core.image_utils import format_image_generation_info, requested_aspect_ratio, restore_source_dimensions

class MessageProcessor:
    def __init__(
        self,
        tenant_config: Any | None = None,
        skills_loader: SkillsLoader | None = None,
        chat_handler: ChatHandler | None = None,
        generated_images_dir: Path | None = None,
    ):
        self.tenant_config = tenant_config
        if chat_handler is not None:
            self.chat_handler = chat_handler
        elif tenant_config is not None:
            self.chat_handler = ChatHandler.from_tenant_config(tenant_config.llm)
        else:
            self.chat_handler = ChatHandler(model_type="gemini")
        # 对话历史记录
        self.conversation_history = {}
        
        # 反思优化相关配置
        self.max_retry_attempts = 2  # 最多重试次数
        self.min_satisfactory_score = 7  # 满意分数阈值
        
        # 记录正在进行的优化任务，避免无限循环
        # 格式: {chat_id: {'attempt': int, 'original_prompt': str, 'reference_images': list}}
        self.optimization_tasks = {}
        self.test_mode = bool(getattr(getattr(tenant_config, "limits", None), "ai_test_mode", False))
        
        # Pro 模式关键词
        self.pro_keywords = [
            "pro模式", "pro 模式", "专业模式", "高级模式",
            "pro", "professional", "高清", "4k", "高质量",
            "精细", "专业版", "pro版"
        ]
        
        # Skills Loader
        workspace_dir = Path(os.getcwd())
        self.skills_loader = skills_loader or SkillsLoader(workspace=workspace_dir)
        self.skill_validation_report = self.skills_loader.validate_skills()
        if self.skill_validation_report["errors"]:
            print(f"⚠️ Skills 校验错误: {self.skill_validation_report['errors']}")
        if self.skill_validation_report["warnings"]:
            print(f"ℹ️ Skills 校验警告: {self.skill_validation_report['warnings']}")
        self.max_tool_iterations = int(
            getattr(getattr(tenant_config, "limits", None), "max_tool_iterations", 5)
        )
        self.generated_images_dir = generated_images_dir or (Path(os.getcwd()) / "generated_images")
        self.generated_images_dir.mkdir(parents=True, exist_ok=True)
        self.message_api_client = None
        self.intent_parser = IntentParser()
        self.conversation_state_manager = ConversationStateManager()
        memory_dir = getattr(tenant_config, "memory_dir", None) or (Path(os.getcwd()) / "memory" / "default")
        self.memory_store = MemoryStore(Path(memory_dir))
        self.execution_compiler = ExecutionCompiler(self.chat_handler, self.skills_loader)
        self.execution_runner = ExecutionRunner(self)

    def _check_pro_mode(self, message: str) -> Tuple[bool, str]:
        """
        检查是否启用 Pro 模式，并返回清理后的消息
        
        Returns:
            Tuple[bool, str]: (是否Pro模式, 清理后的消息)
        """
        msg_lower = message.lower()
        use_pro = False
        clean_message = message
        
        for keyword in self.pro_keywords:
            if keyword in msg_lower:
                use_pro = True
                clean_message = re.sub(re.escape(keyword), '', clean_message, flags=re.IGNORECASE).strip()
                break
        
        return use_pro, clean_message

    def _max_output_images(self) -> int:
        raw = getattr(getattr(self.tenant_config, "limits", None), "max_output_images", 4)
        try:
            return max(1, int(raw))
        except (TypeError, ValueError):
            return 4

    def _chinese_number_to_int(self, raw: str) -> int | None:
        values = {
            "一": 1,
            "二": 2,
            "两": 2,
            "俩": 2,
            "三": 3,
            "四": 4,
            "五": 5,
            "六": 6,
            "七": 7,
            "八": 8,
            "九": 9,
            "十": 10,
        }
        return values.get(raw)

    def _requested_output_count(self, message: str) -> int:
        text = (message or "").strip()
        if not text:
            return 1

        max_output_images = self._max_output_images()
        digit_patterns = (
            r"(?:生成|输出|出|做|来|给我|要|produce|generate|create)\s*([1-9]\d?)\s*(?:张|幅|个|款|版|份|images?|pictures?|pics?|variants?|versions?)",
            r"([1-9]\d?)\s*(?:张|幅|个|款|版|份)\s*(?:图|图片|方案|版本|海报|插画)?",
            r"([1-9]\d?)\s*(?:images?|pictures?|pics?|variants?|versions?)",
        )
        for pattern in digit_patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return max(1, min(int(match.group(1)), max_output_images))

        chinese_match = re.search(r"([一二两俩三四五六七八九十])\s*(?:张|幅|个|款|版|份)", text)
        if chinese_match:
            parsed = self._chinese_number_to_int(chinese_match.group(1))
            if parsed:
                return max(1, min(parsed, max_output_images))

        if re.search(r"(?:多张|多幅|多版|几张|几个方案|several|multiple)", text, flags=re.IGNORECASE):
            return max_output_images

        return 1

    def _looks_like_image_generation_request(self, message: str, has_images: bool = False) -> bool:
        """Cheap local guardrail for obvious draw/generate/edit image requests."""
        text = (message or "").strip().lower()
        if not text:
            return False

        if has_images:
            if requested_aspect_ratio(text, allow_extreme=True):
                return True
            edit_keywords = (
                "改图", "修图", "重绘", "重画", "换背景", "去背景", "抠图", "扩图",
                "风格化", "换成", "生成同款", "做成", "合成", "融合", "编辑图片",
                "重构", "重新构图", "重排", "适配", "适合手机", "手机端", "画幅", "比例", "版式", "构图",
                "edit", "redraw", "restyle", "remix", "remove background", "change background",
                "canvas", "aspect ratio", "recompose",
            )
            return any(keyword in text for keyword in edit_keywords)

        generation_patterns = (
            r"(?:画|画个|画一个|绘制|生成|出图|画图)\s*(?:一?张|一?幅|个|款)?\s*.+",
            r"(?:帮我|给我|请)\s*(?:画|绘制|生成|出图|画图)\s*.+",
            r".+(?:图片|图像|插画|海报|头像|壁纸|logo|图标|表情包|封面|poster|image|picture|illustration|wallpaper|avatar|icon|logo)",
            r"(?:draw|paint|generate|create|make|design)\s+(?:an?|the)?\s*.+",
        )
        return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in generation_patterns)

    def _single_output_task_message(self, message: str, task_index: int, task_count: int) -> str:
        return (
            f"{message}\n\n"
            f"这是多图片输出中的第 {task_index}/{task_count} 个并行任务。"
            "请只生成 1 张图片，并让它与其他任务形成自然不同的方案或变体。"
        )

    def is_self_optimization_message(self, message: str) -> bool:
        """检查是否是自我优化触发的消息"""
        return message.startswith("[优化重试]")

    def parse_optimization_message(self, message: str) -> dict:
        """解析优化消息，提取优化信息"""
        try:
            if not message.startswith("[优化重试]"):
                return None
            
            content = message[len("[优化重试]"):].strip()
            parts = content.split(" | ")
            
            result = {}
            for part in parts:
                if part.startswith("attempt="):
                    result['attempt'] = int(part[len("attempt="):])
                elif part.startswith("original="):
                    result['original_prompt'] = part[len("original="):]
                elif part.startswith("improved="):
                    result['improved_prompt'] = part[len("improved="):]
            
            return result
        except Exception as e:
            print(f"解析优化消息失败: {e}")
            return None

    def create_optimization_message(self, improved_prompt: str, attempt: int, original_prompt: str) -> str:
        """创建优化触发消息"""
        return f"[优化重试] attempt={attempt} | original={original_prompt} | improved={improved_prompt}"

    def determine_skill(
        self,
        message: str,
        has_images: bool = False,
        num_images: int = 0,
        has_files: bool = False,
        num_files: int = 0,
        file_exts: List[str] | None = None
    ) -> str:
        """
        基于技能系统分类消息，决定使用哪个 Skill (完全使用大模型判断)
        """
        if self._looks_like_image_generation_request(message, has_images=has_images):
            return "image_gen"

        skills_summary = self.skills_loader.build_skills_summary()
        if has_images:
            attachment_hint = f"（用户发送了 {num_images} 张图片）"
        elif has_files:
            ext_hint = ",".join(file_exts or [])
            attachment_hint = f"（用户发送了 {num_files} 个文件，类型: {ext_hint or 'unknown'}）"
        else:
            attachment_hint = "（用户发送了纯文本消息）"
        
        classification_prompt = f"""分析以下用户消息，并根据可用的 Skills 判断用户的意图属于哪一个 Skill。

可用 Skills 列表如下:
{skills_summary}

用户消息: {message}
附加信息: {attachment_hint}

请仔细阅读 Skills 列表中的描述，找出最匹配的 skill name。
如果消息为空或只是简单的@机器人，有图片时默认为 image_understanding，有文件时默认为 pdf，无附件时默认为 general。
请只回复匹配的 skill 的 name（例如: image_gen、funny、image_understanding、general），不要包含任何其他内容。"""

        response = self.chat_handler.get_ai_response(
            classification_prompt,
            temperature=0.3
        )
        result = response.strip().lower()
        
        # 验证返回的 skill name 是否有效
        available_skills = [s["name"] for s in self.skills_loader.list_skills()]
        for skill_name in available_skills:
            if skill_name in result:
                return skill_name
                
        # 默认 fallback
        if has_images:
            return "image_understanding"
        if has_files:
            return "pdf"
        return "general"

    def get_funny_response(self, message: str, context: list) -> str:
        """获取幽默回复"""
        funny_prompt = f"""你是一个有点毒舌但本质善良的设计师，请用幽默的方式回应下面的消息。
要求：
- 说话要像真人，不要太正式
- 可以适当阴阳怪气、吐槽
- 用emoji但不要太多
- 语气自然，像朋友聊天

消息: {message}"""

        return self.chat_handler.get_ai_response(
            funny_prompt,
            context=context,
            temperature=0.9
        )

    def _save_generated_image(
        self,
        image_bytes: bytes,
        chat_id: str,
        suffix: str = "",
        reference_image_path: str | None = None,
    ) -> str:
        """保存生成的图片并返回路径"""
        if reference_image_path:
            image_bytes, target_size, generated_size = restore_source_dimensions(
                image_bytes,
                reference_image_path,
            )
            if generated_size != target_size:
                print(
                    "已恢复编辑图分辨率: "
                    f"{generated_size[0]}x{generated_size[1]} -> "
                    f"{target_size[0]}x{target_size[1]}"
                )

        filename = f"{chat_id}_{time.time_ns()}_{uuid.uuid4().hex[:8]}{suffix}.png"
        file_path = self.generated_images_dir / filename
        with open(file_path, 'wb') as f:
            f.write(image_bytes)
        print(f"图片已保存: {file_path}")
        return str(file_path)

    def _image_generation_info_text(
        self,
        image_path: str,
        use_pro: bool = False,
        title: str = "图片信息",
    ) -> str:
        model = (
            self.chat_handler.image_gen_pro_model
            if use_pro
            else self.chat_handler.image_gen_model
        )
        return format_image_generation_info(
            image_path,
            model=model,
            use_pro=use_pro,
            title=title,
        )

    def process_text_message(self, message: str, chat_id: str) -> dict:
        """
        处理纯文字消息
        """
        if chat_id not in self.conversation_history:
            self.conversation_history[chat_id] = []
        
        # 检查是否是优化重试消息
        optimization_info = None
        current_attempt = 0
        original_prompt = message
        
        if self.is_self_optimization_message(message):
            optimization_info = self.parse_optimization_message(message)
            if optimization_info:
                current_attempt = optimization_info.get('attempt', 0)
                original_prompt = optimization_info.get('original_prompt', message)
                message = optimization_info.get('improved_prompt', message)
                print(f"=" * 50)
                print(f"🔄 优化重试 - 第 {current_attempt} 次尝试")
                print(f"📝 原始Prompt: {original_prompt}")
                print(f"✨ 优化后Prompt: {message}")
                print(f"=" * 50)
        
        # 检查 Pro 模式
        use_pro, clean_message = self._check_pro_mode(message)
        if use_pro:
            print(f"🚀 Pro模式启动")
        
        result = self._run_tool_loop(
            message=clean_message,
            chat_id=chat_id,
            has_images=False,
            image_paths=None,
            has_files=False,
            file_paths=None,
            file_exts=None,
            use_pro=use_pro,
            current_attempt=current_attempt,
            original_prompt=original_prompt
        )
        
        # 更新对话历史
        self._update_history(chat_id, message, result.get("text", ""))
        
        return result

    def process_image_message(
        self, 
        message: str, 
        chat_id: str, 
        image_paths: Union[str, List[str]]
    ) -> dict:
        """
        处理带图片的消息（支持1-2张图片）
        """
        # 统一转换为列表格式
        if isinstance(image_paths, str):
            image_paths = [image_paths]
        
        num_images = len(image_paths)
        print(f"📷 处理 {num_images} 张图片")
        
        if chat_id not in self.conversation_history:
            self.conversation_history[chat_id] = []
        
        # 检查是否是优化重试消息
        optimization_info = None
        current_attempt = 0
        original_prompt = message
        
        if self.is_self_optimization_message(message):
            optimization_info = self.parse_optimization_message(message)
            if optimization_info:
                current_attempt = optimization_info.get('attempt', 0)
                original_prompt = optimization_info.get('original_prompt', message)
                message = optimization_info.get('improved_prompt', message)
                print(f"=" * 50)
                print(f"🔄 优化重试（带图片）- 第 {current_attempt} 次尝试")
                print(f"📝 原始Prompt: {original_prompt}")
                print(f"✨ 优化后Prompt: {message}")
                print(f"=" * 50)
        
        # 检查 Pro 模式
        use_pro, clean_message = self._check_pro_mode(message)
        if use_pro:
            print(f"🚀 Pro模式启动")
        
        effective_message = clean_message.strip() if clean_message else ""
        result = self._run_tool_loop(
            message=effective_message,
            chat_id=chat_id,
            has_images=True,
            image_paths=image_paths,
            has_files=False,
            file_paths=None,
            file_exts=None,
            use_pro=use_pro,
            current_attempt=current_attempt,
            original_prompt=original_prompt
        )
        
        # 更新对话历史
        image_count_hint = f"[{num_images}张图片]" if num_images > 1 else "[图片]"
        self._update_history(chat_id, f"{image_count_hint} {message}", result.get("text", ""))
        
        return result

    def process_file_message(
        self,
        message: str,
        chat_id: str,
        file_paths: Union[str, List[str]]
    ) -> dict:
        """
        处理带文件的消息（当前重点支持 PDF）
        """
        if isinstance(file_paths, str):
            file_paths = [file_paths]

        num_files = len(file_paths)
        print(f"📄 处理 {num_files} 个文件")

        if chat_id not in self.conversation_history:
            self.conversation_history[chat_id] = []

        normalized_message = (message or "").strip()
        file_exts = sorted({Path(p).suffix.lower() for p in file_paths if p})

        result = self._run_tool_loop(
            message=normalized_message,
            chat_id=chat_id,
            has_images=False,
            image_paths=None,
            has_files=True,
            file_paths=file_paths,
            file_exts=file_exts,
            use_pro=False,
            current_attempt=0,
            original_prompt=normalized_message
        )

        file_count_hint = f"[{num_files}个文件]"
        self._update_history(chat_id, f"{file_count_hint} {normalized_message}", result.get("text", ""))
        return result

    def reflect_and_decide(self, reflection_context: dict) -> dict:
        """
        执行反思技能，并决定是否重试
        """
        return self.skills_loader.execute_skill(
            name="reflection",
            message="",
            chat_id="",
            processor=self,
            reflection_context=reflection_context
        )

    def _update_history(self, chat_id: str, user_msg: str, assistant_msg: str):
        """更新对话历史"""
        self.conversation_history[chat_id].append({"role": "user", "content": user_msg})
        self.conversation_history[chat_id].append({"role": "assistant", "content": assistant_msg})
        
        if len(self.conversation_history[chat_id]) > 10:
            self.conversation_history[chat_id] = self.conversation_history[chat_id][-10:]

    def _normalize_result(self, result: dict | None) -> dict:
        base = {
            "text": "",
            "image_path": None,
            "image_paths": [],
            "file_path": None,
            "pdf_path": None,
            "needs_reflection": False,
            "reflection_context": None,
            "tool_trace": []
        }
        if not isinstance(result, dict):
            return base
        base.update(result)
        return base

    def _collect_image_paths(self, result: dict) -> list[str]:
        image_paths = []
        image_path = result.get("image_path")
        if image_path:
            image_paths.append(image_path)
        for path in result.get("image_paths") or []:
            if path and path not in image_paths:
                image_paths.append(path)
        return image_paths

    def _format_multiple_image_generation_info(
        self,
        image_paths: list[str],
        use_pro: bool = False,
    ) -> str:
        info_blocks = []
        for index, image_path in enumerate(image_paths, start=1):
            try:
                info_blocks.append(
                    self._image_generation_info_text(
                        image_path,
                        use_pro=use_pro,
                        title=f"图片 {index} 信息",
                    )
                )
            except Exception as exc:
                print(f"读取生成图片信息失败: {image_path}: {exc}")
        return "\n\n".join(info_blocks)

    def _run_parallel_output_tasks(
        self,
        output_count: int,
        message: str,
        chat_id: str,
        has_images: bool,
        image_paths: List[str] | None,
        has_files: bool,
        file_paths: List[str] | None,
        file_exts: List[str] | None,
        use_pro: bool,
        current_attempt: int,
        original_prompt: str,
    ) -> dict:
        print(f"🧩 多图片输出: 并行执行 {output_count} 个单次任务")
        results: list[tuple[int, dict]] = []

        def run_one(task_index: int) -> dict:
            task_message = self._single_output_task_message(message, task_index, output_count)
            return self._run_tool_loop(
                message=task_message,
                chat_id=chat_id,
                has_images=has_images,
                image_paths=image_paths,
                has_files=has_files,
                file_paths=file_paths,
                file_exts=file_exts,
                use_pro=use_pro,
                current_attempt=current_attempt,
                original_prompt=original_prompt,
                allow_parallel_outputs=False,
            )

        with ThreadPoolExecutor(max_workers=output_count) as executor:
            futures = {executor.submit(run_one, index): index for index in range(1, output_count + 1)}
            for future in as_completed(futures):
                index = futures[future]
                try:
                    results.append((index, self._normalize_result(future.result())))
                except Exception as exc:
                    print(f"并行图片任务 {index} 失败: {exc}")
                    results.append((index, self._normalize_result({"text": str(exc)})))

        results.sort(key=lambda item: item[0])
        generated_paths = []
        trace = []
        text_errors = []
        for index, result in results:
            paths = self._collect_image_paths(result)
            if paths:
                generated_paths.extend(path for path in paths if path not in generated_paths)
            else:
                text = (result.get("text") or "").strip()
                if text:
                    text_errors.append(f"任务 {index}: {text}")
            trace.append(
                {
                    "parallel_task": index,
                    "image_count": len(paths),
                    "text_preview": (result.get("text") or "")[:120],
                }
            )

        if generated_paths:
            text = f"已并行生成 {len(generated_paths)} 张图片。"
            if len(generated_paths) < output_count:
                text += f"\n其中 {output_count - len(generated_paths)} 个任务没有返回图片。"
            image_info = self._format_multiple_image_generation_info(
                generated_paths,
                use_pro=use_pro,
            )
            if image_info:
                text += f"\n\n{image_info}"
            return {
                "text": text,
                "image_path": generated_paths[0],
                "image_paths": generated_paths,
                "file_path": None,
                "pdf_path": None,
                "needs_reflection": False,
                "reflection_context": None,
                "tool_trace": trace,
            }

        return {
            "text": "多图片生成失败了。" + (f"\n{'; '.join(text_errors[:3])}" if text_errors else ""),
            "image_path": None,
            "image_paths": [],
            "file_path": None,
            "pdf_path": None,
            "needs_reflection": False,
            "reflection_context": None,
            "tool_trace": trace,
        }

    def _extract_json_object(self, raw: str) -> dict[str, Any]:
        text = (raw or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
            text = re.sub(r"\s*```$", "", text)
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}", text)
            if not match:
                return {}
            try:
                parsed = json.loads(match.group(0))
                return parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                return {}

    def _plan_next_action(
        self,
        user_message: str,
        attachment_hint: str,
        skills_summary: str,
        tools_summary: str,
        trace: list[dict[str, Any]],
        disclosed_skill_context: str
    ) -> dict[str, Any]:
        trace_text = json.dumps(trace[-4:], ensure_ascii=False)
        planning_prompt = f"""你是一个任务编排器。你必须在多轮里决定下一步。

上下文:
- 用户请求: {user_message}
- 附件信息: {attachment_hint}
- 可用工具:
{tools_summary}
- 飞书文档/表格读写: 当用户要读写飞书文档或飞书表格时，使用 action="tool"、tool_name="feishu_docs"，并把参数放进 tool_args。
- Skills 摘要:
{skills_summary}
- 已执行轨迹:
{trace_text}

已披露的 Skill 详情:
{disclosed_skill_context or "暂无"}

【决策流程 - 请按以下步骤分析后再输出】:
第一步: 理解用户的真实意图。用户究竟想要什么结果？
第二步: 判断信息是否充足。执行所选技能是否有关键必要参数缺失（如：需要知道目标语言、具体尺寸、操作类型等）？这些缺失的参数是否可以从上下文合理推断？
第三步: 如果信息充足 → 选择最合适的技能执行（action=tool）；如果已有结果可直接回复 → action=final；仅当关键参数缺失且无法合理推断时 → action=clarify 向用户提问。

输出必须是 JSON，不要输出其他文字:
{{
  "action": "tool" 或 "clarify" 或 "final",
  "tool_name": "当 action=tool 时可选，execute_skill 或 feishu_docs",
  "tool_args": "当 tool_name=feishu_docs 时必填，例如 {{\"action\":\"read_doc\",\"document_id\":\"...\"}}",
  "skill_name": "当 action=tool 时必填，要执行的技能名称",
  "message": "传给 skill 的消息，可为空",
  "clarify_text": "当 action=clarify 时必填，向用户提出的具体澄清问题（要友好、具体，如适用则给出选项或示例）",
  "final_text": "当 action=final 时输出给用户的文本"
}}

规则:
1) 普通技能使用 execute_skill；飞书文档/表格读写使用 feishu_docs。
2) 如果用户请求明确，优先选择最匹配技能并执行，不要多余询问。
3) 仅当关键必要参数缺失且无法合理推断时，才使用 clarify 向用户提问；不要询问可选参数。
4) 如果已有技能执行结果可直接回复，action 用 final。
5) clarify_text 必须具体说明需要什么信息，并给出选项或示例（如果适用）。"""
        response = self.chat_handler.get_ai_response(planning_prompt, temperature=0.2)
        action = self._extract_json_object(response)
        return action

    def _feature_enabled(self, name: str) -> bool:
        features = getattr(self.tenant_config, "features", None)
        if features is None:
            return False
        return bool(getattr(features, name, False))

    def _intent_for_message(
        self,
        *,
        message: str,
        chat_id: str,
        primary_skill: str,
        has_images: bool,
        num_images: int,
        has_files: bool,
        num_files: int,
    ) -> IntentSignal:
        pending = self.conversation_state_manager.get_pending(chat_id)
        if pending and self.intent_parser.looks_like_clarification_reply(message):
            resolved_item = self.conversation_state_manager.resolve_pending(chat_id, message)
            if resolved_item:
                return IntentSignal(
                    action="decompose",
                    items=[resolved_item],
                    parent_item_id=resolved_item.id,
                )
        return self.intent_parser.parse(
            message,
            primary_skill=primary_skill,
            has_images=has_images,
            num_images=num_images,
            has_files=has_files,
            num_files=num_files,
            has_pending_clarification=bool(pending),
        )

    def _maybe_clarify(self, intent: IntentSignal, chat_id: str) -> dict | None:
        if not self._feature_enabled("clarification_loop"):
            return None
        if intent.action != "decompose":
            return None
        ambiguous = next((item for item in intent.items if item.ambiguity_flags), None)
        if not ambiguous:
            return None
        if "subject_count" in ambiguous.ambiguity_flags:
            request = self.conversation_state_manager.create_subject_count_clarification(
                chat_id,
                ambiguous,
            )
            options = " / ".join(option.label for option in request.options)
            return self._normalize_result({"text": f"{request.question}\n可选：{options}"})
        return None

    def _compile_and_execute(
        self,
        *,
        intent: IntentSignal,
        message: str,
        chat_id: str,
        primary_skill: str,
        image_paths: List[str] | None,
        file_paths: List[str] | None,
        file_exts: List[str] | None,
        use_pro: bool,
        original_prompt: str,
    ) -> dict:
        memory_snippets: list[str] = []
        if self._feature_enabled("memory_system"):
            try:
                self.memory_store.ensure_exists()
                memory_snippets = self.memory_store.snippets_for(
                    message,
                    skills=[primary_skill] if primary_skill else None,
                )
            except Exception as exc:
                print(f"Memory lookup skipped: {exc}")

        plan = self.execution_compiler.compile(
            intent=intent,
            original_message=message,
            primary_skill=primary_skill,
            image_paths=image_paths,
            file_paths=file_paths,
            use_pro=use_pro,
            memory_snippets=memory_snippets,
        )
        result = self.execution_runner.execute_plan(
            plan,
            chat_id=chat_id,
            original_prompt=original_prompt,
            file_exts=file_exts,
        )
        result["compiled_plan"] = {
            "summary": plan.summary,
            "execution_mode": plan.execution_mode,
            "task_count": len(plan.tasks),
        }
        return result

    def _run_tool_loop(
        self,
        message: str,
        chat_id: str,
        has_images: bool,
        image_paths: List[str] | None,
        has_files: bool,
        file_paths: List[str] | None,
        file_exts: List[str] | None,
        use_pro: bool,
        current_attempt: int,
        original_prompt: str,
        allow_parallel_outputs: bool = True,
    ) -> dict:
        registry = ToolRegistry()
        registry.register(ExecuteSkillTool())
        registry.register(FeishuDocsTool())
        num_images = len(image_paths or [])
        num_files = len(file_paths or [])
        attachment_hint = "纯文本"
        if has_images:
            attachment_hint = f"{num_images}张图片"
        if has_files:
            attachment_hint = f"{num_files}个文件({','.join(file_exts or [])})"
        primary_skill = self.determine_skill(
            message=message,
            has_images=has_images,
            num_images=num_images,
            has_files=has_files,
            num_files=num_files,
            file_exts=file_exts
        )
        if current_attempt > 0 and not has_files:
            primary_skill = "image_gen"
        print(f"📋 首选 Skill: {primary_skill}")
        if current_attempt == 0 and allow_parallel_outputs and self._feature_enabled("compile_and_execute"):
            intent = self._intent_for_message(
                message=message,
                chat_id=chat_id,
                primary_skill=primary_skill,
                has_images=has_images,
                num_images=num_images,
                has_files=has_files,
                num_files=num_files,
            )
            clarification = self._maybe_clarify(intent, chat_id)
            if clarification:
                return clarification
            if intent.action == "decompose":
                try:
                    return self._compile_and_execute(
                        intent=intent,
                        message=message,
                        chat_id=chat_id,
                        primary_skill=primary_skill,
                        image_paths=image_paths,
                        file_paths=file_paths,
                        file_exts=file_exts,
                        use_pro=use_pro,
                        original_prompt=original_prompt,
                    )
                except Exception as exc:
                    print(f"Compile-and-execute fallback to v1: {exc}")
        output_count = self._requested_output_count(message)
        if allow_parallel_outputs and current_attempt == 0 and primary_skill == "image_gen" and output_count > 1:
            return self._run_parallel_output_tasks(
                output_count=output_count,
                message=message,
                chat_id=chat_id,
                has_images=has_images,
                image_paths=image_paths,
                has_files=has_files,
                file_paths=file_paths,
                file_exts=file_exts,
                use_pro=use_pro,
                current_attempt=current_attempt,
                original_prompt=original_prompt,
            )
        runtime = {
            "processor": self,
            "skills_loader": self.skills_loader,
            "chat_id": chat_id,
            "message": message,
            "has_images": has_images,
            "image_paths": image_paths,
            "has_files": has_files,
            "file_paths": file_paths,
            "file_exts": file_exts,
            "use_pro": use_pro,
            "current_attempt": current_attempt,
            "original_prompt": original_prompt,
            "message_api_client": self.message_api_client,
        }
        trace: list[dict[str, Any]] = []
        if primary_skill == "image_gen":
            print("🛠️ 直通调用技能: image_gen")
            execution = registry.execute(
                "execute_skill",
                {
                    "skill_name": "image_gen",
                    "message": message,
                    "reason": "direct_image_request",
                },
                runtime=runtime,
            )
            normalized = self._normalize_result(execution.get("result"))
            normalized["tool_trace"] = [
                {
                    "round": 1,
                    "skill": "image_gen",
                    "text_preview": (normalized.get("text") or "")[:120],
                }
            ]
            return normalized

        disclosed_skills: set[str] = set()
        last_result = self._normalize_result({})
        available_skills = {s["name"] for s in self.skills_loader.list_skills()}
        for idx in range(self.max_tool_iterations):
            disclosed_context = self.skills_loader.load_skills_for_context(sorted(disclosed_skills)) if disclosed_skills else ""
            action = self._plan_next_action(
                user_message=message,
                attachment_hint=attachment_hint,
                skills_summary=self.skills_loader.build_skills_summary(),
                tools_summary=registry.to_prompt_summary(),
                trace=trace,
                disclosed_skill_context=disclosed_context
            )
            action_type = str(action.get("action", "tool")).strip().lower()
            if action_type == "final":
                final_text = str(action.get("final_text", "")).strip()
                if final_text:
                    last_result["text"] = final_text
                    last_result["tool_trace"] = trace
                    return last_result
            if action_type == "clarify":
                clarify_text = str(action.get("clarify_text", "")).strip()
                if clarify_text:
                    print(f"❓ 向用户提问澄清: {clarify_text[:80]}...")
                    last_result["text"] = clarify_text
                    last_result["tool_trace"] = trace
                    return last_result
            tool_name = str(action.get("tool_name", "")).strip()
            if tool_name == "feishu_docs":
                tool_args = action.get("tool_args")
                if not isinstance(tool_args, dict):
                    tool_args = {
                        key: value
                        for key, value in action.items()
                        if key not in {"action", "tool_name", "skill_name", "message", "reason"}
                    }
                print(f"🛠️ 第{idx + 1}轮调用工具: feishu_docs")
                normalized = self._normalize_result(
                    registry.execute("feishu_docs", tool_args, runtime=runtime)
                )
                preview = (normalized.get("text") or "")[:120]
                trace.append({"round": idx + 1, "tool": "feishu_docs", "text_preview": preview})
                normalized["tool_trace"] = trace
                last_result = normalized
                return normalized
            skill_name = str(action.get("skill_name", "")).strip() or primary_skill
            if skill_name not in available_skills:
                skill_name = primary_skill
            disclosed_skills.add(skill_name)
            tool_payload = {
                "skill_name": skill_name,
                "message": str(action.get("message", message)),
                "reason": f"round_{idx + 1}"
            }
            print(f"🛠️ 第{idx + 1}轮调用技能: {skill_name}")
            execution = registry.execute("execute_skill", tool_payload, runtime=runtime)
            normalized = self._normalize_result(execution.get("result"))
            preview = (normalized.get("text") or "")[:120]
            trace.append({"round": idx + 1, "skill": skill_name, "text_preview": preview})
            normalized["tool_trace"] = trace
            last_result = normalized
            if normalized.get("image_path") or normalized.get("image_paths") or normalized.get("file_path") or normalized.get("pdf_path"):
                return normalized
            if normalized.get("needs_reflection"):
                return normalized
            message = normalized.get("text", "") or message
            runtime["message"] = message
            primary_skill = skill_name
        return last_result
