def execute(message: str, chat_id: str, processor, **kwargs) -> dict:
    """
    Execute the image understanding skill.
    """
    has_images = kwargs.get("has_images", False)
    image_paths = kwargs.get("image_paths", [])
    num_images = len(image_paths) if image_paths else 0

    result = {
        "text": "", 
        "image_path": None,
        "needs_reflection": False,
        "reflection_context": None
    }

    if not has_images or not image_paths:
        result["text"] = "请发送需要分析的图片。"
        return result

    if num_images == 1:
        prompt = message if message else """作为资深平面设计师，请从以下维度点评这张图片：

**构图与布局**：视觉重心、空间分配、留白运用
**色彩运用**：配色方案是否和谐、色彩情感、主色/辅色/点缀色
**字体排版**（如有）：字体选择、可读性、视觉层级
**视觉层次**：信息优先级是否清晰、引导视线的方式
**整体印象**：风格定位、亮点与不足、具体改进建议

用口语化的设计师语气，简洁有力，重点突出。"""
    else:
        prompt = message if message else """作为资深平面设计师，请对比分析这几张图片：

**风格一致性**：视觉语言是否统一
**各自亮点**：每张图的设计优点
**构图与色彩对比**：差异在哪，各自效果如何
**综合建议**：如果要选一张或融合优点，你会怎么做

用口语化的设计师语气，给出有价值的专业判断。"""
    
    result["text"] = processor.chat_handler.understand_images(
        image_paths=image_paths,
        user_prompt=prompt,
        context=processor.conversation_history.get(chat_id, [])
    )
        
    return result
