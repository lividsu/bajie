# humanized_responses.py
"""
人性化回复模块 - 让机器人像一个真实的设计师 (使用 FAST_MODEL 动态生成)
"""
from typing import List, Optional

class DesignerPersonality:
    """设计师人格化回复生成器"""
    
    def __init__(self):
        self.chat_handler = None
        
    def _generate(self, prompt: str, fallback: str = "我先看一下。") -> str:
        try:
            if self.chat_handler is None:
                from llm.chat_client import ChatHandler

                self.chat_handler = ChatHandler()
            reply = self.chat_handler.get_ai_response(
                user_message=prompt,
                temperature=0.8,
                max_tokens=100
            ).strip(' "')
            return self._usable_reply(reply, fallback)
        except Exception as e:
            return fallback

    def _usable_reply(self, reply: str, fallback: str) -> str:
        reply = (reply or "").strip()
        hollow_replies = {"好的", "好的。", "没问题", "没问题。", "收到", "收到。"}
        if (
            not reply
            or reply in hollow_replies
            or "马上处理" in reply
            or "I met some problems" in reply
        ):
            return fallback
        return reply

    def _format_points(self, points: Optional[List[str]], fallback: str) -> str:
        clean_points = []
        for point in points or []:
            point = str(point).strip().strip("。；;，, ")
            if point:
                clean_points.append(point)
        if not clean_points:
            return fallback
        return "；".join(clean_points[:3])

    def _brief(self, text: str, fallback: str, limit: int = 90) -> str:
        text = (text or "").strip()
        if not text:
            return fallback
        text = " ".join(text.split())
        if len(text) > limit:
            return text[:limit].rstrip("，,。；; ") + "..."
        return text.strip("。 ")
            
    def get_starting_image_gen(self, use_pro: bool = False) -> str:
        """获取开始生成图片的回复"""
        pro_hint = "并提一下你开启了高清专业模式会更精细，" if use_pro else ""
        prompt = f"你是一名资深平面设计师。请用一句简短自然的口语（15字以内），告诉用户你马上开始构思创作了。{pro_hint}直接输出回复，不要任何前缀。"
        return self._generate(prompt, "我先构思一下。")

    def get_image_gen_success(self, use_pro: bool = False) -> str:
        """获取图片生成成功的回复"""
        pro_hint = "提一下这是用专业模式生成的，质量更高，" if use_pro else ""
        prompt = f"你是一名资深平面设计师。请用一句简短自然的口语（15字以内），告诉用户作品出来了，让他看看。{pro_hint}直接输出回复，不要任何前缀。"
        return self._generate(prompt, "图出来了，你看看。")

    def get_image_gen_failed(self) -> str:
        """获取图片生成失败的回复"""
        prompt = "你是一名资深平面设计师。请用一句简短、有点遗憾的口语（15字以内），告诉用户这次生成失败了，建议换个描述方式或更具体一些。直接输出回复，不要任何前缀。"
        return self._generate(prompt, "这次没生成好，描述再具体点试试。")

    def get_starting_image_edit(self) -> str:
        """获取开始编辑图片的回复"""
        prompt = "你是一名资深平面设计师。请用一句简短自然的口语（15字以内），告诉用户你马上开始帮他修改这张图。直接输出回复，不要任何前缀。"
        return self._generate(prompt, "我来改这张图。")

    def get_image_edit_success(self) -> str:
        """获取图片编辑成功的回复"""
        prompt = "你是一名资深平面设计师。请用一句简短自然的口语（15字以内），告诉用户图已改好，让他看看效果。直接输出回复，不要任何前缀。"
        return self._generate(prompt, "图改好了，你看看。")

    def get_starting_image_understand(self) -> str:
        """获取开始图片理解的回复"""
        prompt = "你是一名资深平面设计师。请用一句简短自然的口语（15字以内），告诉用户你正在从设计角度仔细看这张图，马上给出分析。直接输出回复，不要任何前缀。"
        return self._generate(prompt, "我仔细看看这张图。")

    def get_starting_reflection(self) -> str:
        """获取开始反思的回复"""
        prompt = "你是一名对作品质量要求极高的资深设计师。请用一句简短自然的口语（15字以内），告诉用户你要先用专业眼光审视一下这张作品。直接输出回复，不要任何前缀。"
        return self._generate(prompt, "我先审一遍效果。")

    def get_reflection_need_improve(self, score: int, issues: List[str], analysis: str = "") -> str:
        """获取需要优化的反思回复"""
        issues_text = self._format_points(issues, "构图、色彩或细节还不够稳")
        analysis_text = self._brief(analysis, "", 80)
        lines = [
            f"🤔 自检 {score}/10，这版我先不放过。",
            f"问题在于：{issues_text}。",
        ]
        if analysis_text:
            lines.append(f"整体看：{analysis_text}。")
        lines.append("我会按这些点再优化一版。")
        return "\n".join(lines)

    def get_reflection_satisfied(
        self,
        score: int,
        analysis: str = "",
        issues: Optional[List[str]] = None,
        strengths: Optional[List[str]] = None,
    ) -> str:
        """获取满意的反思回复"""
        if score < 7 or issues:
            issues_text = self._format_points(issues, "还没达到可直接交付的稳定度")
            analysis_text = self._brief(analysis, "", 80)
            lines = [
                f"⚠️ 自检 {score}/10，这版不建议直接过。",
                f"问题在于：{issues_text}。",
            ]
            if analysis_text:
                lines.append(f"整体看：{analysis_text}。")
            return "\n".join(lines)

        strengths_text = self._format_points(
            strengths,
            self._brief(analysis, "构图、色彩和画面完整度都比较稳", 80),
        )
        return f"✅ 自检 {score}/10，可以过。\n好在：{strengths_text}。"

    def get_multi_image_notice(self, max_n: int, total: int) -> str:
        """获取多图片限制提示"""
        prompt = f"你是一名设计师。用户发了{total}张图，你一次最多处理{max_n}张。请用简短友好的口语（15字以内）告诉用户你先看前{max_n}张。直接输出回复。"
        return f"({self._generate(prompt, f'我先处理前{max_n}张。')})"

    def get_image_info_failed(self) -> str:
        """获取获取图片信息失败的回复"""
        prompt = "你是一名资深平面设计师。请用一句简短自然的口语（15字以内），告诉用户图片信息读取失败了，带点疑惑语气。直接输出回复，不要任何前缀。"
        return self._generate(prompt, "这张图我没读到。")

    def get_image_process_failed(self) -> str:
        """获取处理图片失败的回复"""
        prompt = "你是一名资深平面设计师。请用一句简短自然的口语（15字以内），告诉用户处理图片时出了点问题，带点抱歉语气。直接输出回复，不要任何前缀。"
        return self._generate(prompt, "处理时卡住了，抱歉。")

    def get_empty_message_reply(self) -> str:
        """获取收到空消息的回复"""
        prompt = "你是一名资深平面设计师。请用一句简短自然的口语（15字以内），调侃用户发了个空消息。直接输出回复，不要任何前缀。"
        return self._generate(prompt, "你这条消息有点太留白了。")

    def get_empty_text_reply(self) -> str:
        """获取收到纯艾特无文字的回复"""
        prompt = "你是一名资深平面设计师。请用一句简短自然的口语（15字以内），吐槽用户艾特你但没说要干啥。直接输出回复，不要任何前缀。"
        return self._generate(prompt, "喊我啦，想做哪张图？")

    def get_thinking(self) -> str:
        """获取思考中的回复"""
        prompt = "你是一名资深平面设计师。请用两三个字，表达你正在思考设计方案（比如：让我想想、嗯...）。直接输出回复。"
        return self._generate(prompt, "我想想。")

    def humanize_response(self, technical_response: str, context: str = "general") -> str:
        """将技术性回复转换为更人性化的表达"""
        prompt = f"请将以下机械或技术性的回复，用资深平面设计师的口吻重新表达，要求自然口语化、有设计专业感、不做作，去掉空洞套话（如'好的'、'没问题'）。\n原回复：{technical_response}\n直接输出优化后的回复。"
        return self._generate(prompt, technical_response)


class ResponseBuilder:
    """回复构建器 - 组合人性化回复"""
    
    def __init__(self):
        self.personality = DesignerPersonality()
    
    def build_image_gen_response(
        self, 
        success: bool, 
        use_pro: bool = False,
        custom_message: str = ""
    ) -> str:
        """构建图片生成的回复"""
        if success:
            base = self.personality.get_image_gen_success(use_pro)
            if custom_message:
                return f"{base}\n{custom_message}"
            return base
        else:
            return self.personality.get_image_gen_failed()
    
    def build_reflection_response(
        self,
        should_retry: bool,
        score: int,
        analysis: str,
        issues: List[str],
        attempt: int,
        strengths: Optional[List[str]] = None,
    ) -> str:
        """构建反思回复"""
        if should_retry:
            return self.personality.get_reflection_need_improve(score, issues, analysis)
        else:
            return self.personality.get_reflection_satisfied(score, analysis, issues, strengths)
    
    def build_retry_notice(self, attempt: int, improved_prompt: str) -> str:
        """构建重试通知"""
        notice = f"🔄 第{attempt}次优化中..."
        return notice


# 导出
designer = DesignerPersonality()
response_builder = ResponseBuilder()
