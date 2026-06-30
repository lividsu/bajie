def _join_points(points, fallback: str) -> str:
    clean = [str(point).strip().strip("。；; ") for point in points or [] if str(point).strip()]
    return "；".join(clean[:3]) if clean else fallback


def _build_reflection_text(reflection_result: dict, should_retry: bool, improved_prompt: str, min_score: int) -> str:
    score = reflection_result.get("score", 0)
    analysis = str(reflection_result.get("analysis") or "").strip()
    issues = reflection_result.get("issues") or []
    strengths = reflection_result.get("strengths") or []

    if should_retry:
        issues_text = _join_points(issues, "生成结果和原始需求还有明显偏差")
        lines = [
            f"⚠️ 自检 {score}/10，低于通过线 {min_score}/10，我会重画一版。",
            f"问题：{issues_text}。",
        ]
        if analysis:
            lines.append(f"判断：{analysis}")
        lines.extend([
            "新的 prompt：",
            improved_prompt,
        ])
        return "\n".join(lines)

    strengths_text = _join_points(strengths, analysis or "主体和画面完成度达到当前需求")
    lines = [
        f"✅ 自检 {score}/10，达到通过线 {min_score}/10，不需要重画。",
        f"通过原因：{strengths_text}。",
    ]
    if issues:
        lines.append(f"轻微瑕疵：{_join_points(issues, '有少量细节可继续打磨')}。")
    return "\n".join(lines)


def _has_hard_failure(reflection_result: dict) -> bool:
    text = " ".join(
        str(part)
        for part in [
            reflection_result.get("analysis", ""),
            " ".join(str(issue) for issue in reflection_result.get("issues") or []),
        ]
    ).lower()
    hard_failure_keywords = (
        "主体缺失",
        "没有主体",
        "缺少主体",
        "数量错误",
        "数量不对",
        "模式错误",
        "关键约束违背",
        "不符合白底",
        "subject missing",
        "missing subject",
        "wrong count",
        "count mismatch",
        "mode mismatch",
        "violates key constraint",
    )
    return any(keyword in text for keyword in hard_failure_keywords)


def execute(message: str, chat_id: str, processor, **kwargs) -> dict:
    """
    Execute the reflection skill.
    """
    reflection_context = kwargs.get("reflection_context", {})
    
    generated_image_path = reflection_context.get("generated_image_path")
    original_prompt = reflection_context.get("original_prompt")
    reference_images = reflection_context.get("reference_images")
    current_attempt = reflection_context.get("attempt", 0)
    
    print(f"=" * 50)
    print(f"🔍 开始自我反思 - 第 {current_attempt + 1} 次检查")
    print(f"📝 原始需求: {original_prompt}")
    print(f"=" * 50)
    
    # 调用反思方法
    reflection_result = processor.chat_handler.reflect_on_generated_image(
        generated_image_path=generated_image_path,
        original_prompt=original_prompt,
        reference_image_paths=reference_images
    )
    
    print(f"📊 反思结果:")
    print(f"   - 分数: {reflection_result['score']}/10")
    print(f"   - 满意: {'是' if reflection_result['is_satisfactory'] else '否'}")
    print(f"   - 分析: {reflection_result['analysis']}")
    if reflection_result.get('strengths'):
        print(f"   - 优点: {reflection_result['strengths']}")
    if reflection_result['issues']:
        print(f"   - 问题: {reflection_result['issues']}")
    if reflection_result.get('improved_prompt'):
        print(f"   - 优化Prompt: {reflection_result['improved_prompt']}")
    
    # 测试模式强制低分
    if processor.test_mode:
        reflection_result['score'] = 3
        reflection_result['is_satisfactory'] = False
        if not reflection_result.get('improved_prompt'):
            base_prompt = original_prompt or reflection_context.get("current_prompt") or ""
            reflection_result['improved_prompt'] = f"请提高细节和清晰度：{base_prompt}"
        print("⚠️ 测试模式：强制低分触发重试")

    try:
        score = int(reflection_result.get('score', 0))
    except (TypeError, ValueError):
        score = 0
    min_score = getattr(processor, "min_satisfactory_score", 7)
    score_passed = score >= min_score
    hard_failure = _has_hard_failure(reflection_result)
    if hard_failure:
        score_passed = False
        reflection_result['is_satisfactory'] = False
        print("⚠️ 检测到硬失败条件，本轮自检强制不通过")
    if score_passed and not reflection_result.get('is_satisfactory'):
        print(f"✅ 分数 {score}/10 已达到阈值 {min_score}/10，覆盖模型的未通过判断")
    reflection_result['score'] = score
    reflection_result['is_satisfactory'] = bool(reflection_result.get('is_satisfactory')) or score_passed
    
    result = {
        'should_retry': False,
        'text': "",
        'optimization_message': None,
        'reference_images': reference_images
    }
    
    # 判断是否需要重试
    if not reflection_result['is_satisfactory'] and current_attempt < processor.max_retry_attempts:
        improved_prompt = str(reflection_result.get('improved_prompt') or '').strip()
        if not improved_prompt:
            base_prompt = original_prompt or reflection_context.get("current_prompt") or "重新生成这张图"
            improved_prompt = f"{base_prompt}。请严格贴合原始需求，修正自检指出的问题，保持主体明确、构图清晰、画面干净。"
        if improved_prompt:
            result['should_retry'] = True
            result['optimization_message'] = processor.create_optimization_message(
                improved_prompt=improved_prompt,
                attempt=current_attempt + 1,
                original_prompt=original_prompt
            )
            print(f"=" * 50)
            print(f"🔄 决定重试！")
            print(f"✨ 改进后的Prompt: {improved_prompt}")
            print(f"=" * 50)
            result["text"] = _build_reflection_text(
                reflection_result=reflection_result,
                should_retry=True,
                improved_prompt=improved_prompt,
                min_score=min_score,
            )
    else:
        print(f"✅ 不需要重试，结果{'满意' if reflection_result['is_satisfactory'] else '已达最大尝试次数'}")
        result["text"] = _build_reflection_text(
            reflection_result=reflection_result,
            should_retry=False,
            improved_prompt="",
            min_score=min_score,
        )
    
    return result
