DESIGNER_SYSTEM_PROMPT = """你是一名资深平面设计师，精通以下领域：
- 视觉设计原则（对比、对齐、重复、亲密性 CARP 原则）
- 色彩理论（色相、饱和度、明度、互补色、邻近色、分裂互补）
- 字体排版（字重、字距、行距、视觉层级、中西文混排）
- 品牌设计与视觉识别系统（VI）
- UI/UX 设计（信息架构、交互逻辑、用户体验）
- 印刷设计规范（出血、CMYK、分辨率）
- 数字媒体设计（RGB、屏幕适配、响应式）
- 常用设计工具（Figma、Adobe Photoshop、Illustrator、InDesign、Sketch）
- 国内外设计趋势与审美

回答时用自然、专业的口语化风格，像资深设计师朋友聊天一样给出实用建议，避免空洞套话。涉及专业概念时顺带解释。"""


def execute(message: str, chat_id: str, processor, **kwargs) -> dict:
    """
    Execute the general conversation skill with designer expertise.
    """
    result = {
        "text": "",
        "image_path": None,
        "needs_reflection": False,
        "reflection_context": None
    }

    designer_message = f"{DESIGNER_SYSTEM_PROMPT}\n\n用户问题：{message}"

    result["text"] = processor.chat_handler.get_ai_response(
        designer_message,
        context=processor.conversation_history.get(chat_id, [])
    )

    return result