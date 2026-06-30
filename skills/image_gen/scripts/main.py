from llm.humanized_responses import designer, response_builder
from core.image_utils import get_display_size, recommended_image_size, requested_aspect_ratio


def _is_canvas_recompose_request(message: str, target_aspect_ratio: str | None) -> bool:
    """Detect requests that need model recomposition instead of preserving the source canvas."""
    if target_aspect_ratio:
        return True
    normalized = (message or "").lower()
    keywords = (
        "重构", "重绘", "重新构图", "重排", "适配", "适合手机", "手机端",
        "画幅", "比例", "版式", "构图", "canvas", "aspect ratio", "recompose",
    )
    return any(keyword in normalized for keyword in keywords)


def _enhance_prompt(processor, message: str) -> str:
    """用设计师视角丰富用户的生成提示词，使图片效果更专业。"""
    if not message or len(message.strip()) < 5:
        return message
    enhance_prompt = f"""你是一名资深平面设计师兼 AI 绘图专家。用户想生成一张图片，原始描述如下：

"{message}"

请基于原始描述，输出一段更详细、更专业的英文图片生成提示词（prompt）。要求：
- 保留原意，不改变主题
- 补充构图方式（如 center composition、rule of thirds）
- 补充色彩风格（如 warm tones、vibrant colors、muted palette）
- 补充光线与氛围（如 soft natural light、cinematic lighting）
- 补充风格标签（如 flat design、illustration、photorealistic、minimalist）
- 结尾加上质量标签：high quality, detailed, 4K

只输出优化后的英文 prompt，不要任何解释或前缀。"""
    try:
        enhanced = processor.chat_handler.get_ai_response(enhance_prompt, temperature=0.4)
        enhanced = enhanced.strip().strip('"').strip("'")
        if enhanced and len(enhanced) > 10:
            print(f"✨ Prompt 增强: {enhanced[:120]}...")
            return enhanced
    except Exception:
        pass
    return message


def execute(message: str, chat_id: str, processor, **kwargs) -> dict:
    """
    Execute the image generation/editing skill.
    """
    has_images = kwargs.get("has_images", False)
    image_paths = kwargs.get("image_paths", [])
    num_images = len(image_paths) if image_paths else 0
    use_pro = kwargs.get("use_pro", False)
    task_mode = kwargs.get("task_mode") or ("edit" if has_images else "generate")
    current_attempt = kwargs.get("current_attempt", 0)
    original_prompt = kwargs.get("original_prompt", message)

    result = {
        "text": "",
        "image_path": None,
        "needs_reflection": False,
        "reflection_context": None
    }

    if not has_images:
        # Enhance prompt with designer expertise before generation
        enhanced_message = _enhance_prompt(processor, message)

        # Text to Image
        text_response, image_bytes = processor.chat_handler.generate_image(
            enhanced_message,
            use_pro=use_pro
        )
        
        if image_bytes:
            suffix = "_pro" if use_pro else "_gen"
            result["image_path"] = processor._save_generated_image(image_bytes, chat_id, suffix)
            result["text"] = response_builder.build_image_gen_response(True, use_pro)
            result["text"] += "\n\n" + processor._image_generation_info_text(
                result["image_path"],
                use_pro=use_pro,
            )
            
            if current_attempt < processor.max_retry_attempts:
                result["needs_reflection"] = True
                result["reflection_context"] = {
                    "generated_image_path": result["image_path"],
                    "original_prompt": original_prompt,
                    "current_prompt": message,
                    "reference_images": None,
                    "attempt": current_attempt,
                    "use_pro": use_pro
                }
        else:
            result["text"] = response_builder.build_image_gen_response(False)
            if text_response:
                result["text"] += f"\n({text_response})"
    else:
        # Image to Image (Editing/Styling)
        if num_images == 1:
            prompt = message if message else "基于这张图片进行创意改编"
        else:
            if task_mode == "compose":
                prompt = message if message else "基于这些图片进行创意合成或融合"
            else:
                prompt = message if message else "基于这些图片执行指定编辑，保持输出目标清晰"

        target_aspect_ratio = requested_aspect_ratio(message, allow_extreme=True)
        recompose_canvas = num_images == 1 and _is_canvas_recompose_request(message, target_aspect_ratio)
        output_image_size = None
        if recompose_canvas:
            if target_aspect_ratio:
                source_w, source_h = get_display_size(image_paths[0])
                output_image_size = recommended_image_size(source_w, source_h)
            aspect_hint = target_aspect_ratio or "the requested mobile-friendly aspect ratio"
            prompt = (
                f"{prompt}\n\n"
                "Canvas task: use the image generation model to reconstruct and recompose "
                f"the picture for a {aspect_hint} canvas. Preserve the important subject, "
                "visual identity, and style, but adapt the layout naturally for the new "
                "format. Extend or redraw background/content where needed. Do not simply "
                "stretch, crop, pad, or mechanically resize the original image."
            )

        text_response, image_bytes = processor.chat_handler.generate_image_with_references(
            image_paths=image_paths,
            user_prompt=prompt,
            use_pro=use_pro,
            preserve_reference_resolution=(num_images == 1 and not recompose_canvas),
            output_aspect_ratio=target_aspect_ratio if recompose_canvas else None,
            output_image_size=output_image_size,
        )
        
        if image_bytes:
            suffix = "_pro_edited" if use_pro else "_edited"
            result["image_path"] = processor._save_generated_image(
                image_bytes,
                chat_id,
                suffix,
                reference_image_path=image_paths[0] if num_images == 1 and not recompose_canvas else None,
            )
            if num_images > 1:
                result["text"] = designer.get_image_edit_success() + " (基于多张图片)"
            else:
                result["text"] = designer.get_image_edit_success()
            result["text"] += "\n\n" + processor._image_generation_info_text(
                result["image_path"],
                use_pro=use_pro,
            )
            
            if current_attempt < processor.max_retry_attempts:
                result["needs_reflection"] = True
                result["reflection_context"] = {
                    "generated_image_path": result["image_path"],
                    "original_prompt": original_prompt if original_prompt else prompt,
                    "current_prompt": prompt,
                    "reference_images": image_paths,
                    "attempt": current_attempt,
                    "use_pro": use_pro
                }
        else:
            result["text"] = designer.get_image_gen_failed()
            if text_response:
                result["text"] += f"\n({text_response})"
                
    return result
