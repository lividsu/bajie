def execute(message: str, chat_id: str, processor, **kwargs) -> dict:
    """
    Execute the funny response skill.
    """
    has_images = kwargs.get("has_images", False)
    image_paths = kwargs.get("image_paths", [])

    result = {
        "text": "", 
        "image_path": None,
        "needs_reflection": False,
        "reflection_context": None
    }

    if has_images:
        prompt = f"请以幽默、吐槽的方式回应这张图片。{message}"
        result["text"] = processor.chat_handler.understand_images(
            image_paths=image_paths,
            user_prompt=prompt,
            context=processor.conversation_history.get(chat_id, [])
        )
    else:
        result["text"] = processor.get_funny_response(message, processor.conversation_history.get(chat_id, []))
        
    return result