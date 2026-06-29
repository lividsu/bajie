# chat.py
from typing import Optional, Tuple, List, Union, Any
import os
import base64
from openai import OpenAI
from google import genai
from google.genai import types
from core.image_utils import (
    closest_supported_aspect_ratio,
    get_display_size,
    recommended_image_size,
)


class ChatHandler:
    def __init__(
        self,
        model_type: str = "gemini",
        api_keys: dict[str, str | None] | None = None,
        models: dict[str, str] | None = None,
    ):
        """
        Initialize chat handler
        
        Args:
            model_type: Type of model to use ("openai", "deepseek", or "gemini")
        """
        api_keys = api_keys or {}

        def get_key(provider: str) -> str | None:
            return api_keys.get(provider)

        self.model_config = {
            "openai": {
                "api_key": get_key("openai"),
                "base_url": None,
            },
            "deepseek": {
                "api_key": get_key("deepseek"),
                "base_url": "https://api.deepseek.com",
            },
            "gemini": {
                "api_key": get_key("gemini") or api_keys.get("google"),
                "base_url": None,
            }
        }
        
        if model_type not in self.model_config:
            raise ValueError(f"Unsupported model type: {model_type}")
            
        config = self.model_config[model_type]
        if not config["api_key"]:
            raise ValueError(f"{model_type.upper()}_API_KEY is not configured")
        
        self.model_type = model_type
        
        if model_type == "gemini":
            self.client = genai.Client(api_key=config["api_key"])
        else:
            self.client = OpenAI(
                api_key=config["api_key"],
                base_url=config["base_url"]
            )
        
        # 抽象的模型角色
        models = models or {}
        self.fast_model = models.get("fast_model") or "gemini-3-flash-preview"
        self.judge_model = models.get("judge_model") or "gemini-3-flash-preview"
        self.image_gen_model = models.get("image_gen_model") or "gemini-3.1-flash-image-preview"
        self.image_gen_pro_model = models.get("image_gen_pro_model") or "gemini-3-pro-image-preview"
        self.image_understand_model = models.get("image_understand_model") or "gemini-3-pro-preview"

    @classmethod
    def from_tenant_config(cls, llm_config: Any) -> "ChatHandler":
        return cls(
            model_type=llm_config.provider,
            api_keys={
                "openai": llm_config.openai_api_key,
                "deepseek": llm_config.deepseek_api_key,
                "gemini": llm_config.gemini_api_key,
                "google": llm_config.google_api_key,
            },
            models={
                "fast_model": llm_config.fast_model,
                "judge_model": llm_config.judge_model,
                "image_gen_model": llm_config.image_gen_model,
                "image_gen_pro_model": llm_config.image_gen_pro_model,
                "image_understand_model": llm_config.image_understand_model,
            },
        )

    def _get_mime_type(self, image_path: str) -> str:
        """根据文件扩展名获取MIME类型"""
        ext = os.path.splitext(image_path)[1].lower()
        mime_type_map = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.webp': 'image/webp',
            '.heic': 'image/heic',
            '.heif': 'image/heif'
        }
        return mime_type_map.get(ext, 'image/jpeg')

    def _load_image_as_part(self, image_path: str) -> types.Part:
        """加载图片文件并转换为 Gemini Part 对象"""
        with open(image_path, 'rb') as f:
            image_bytes = f.read()
        
        mime_type = self._get_mime_type(image_path)
        
        return types.Part.from_bytes(
            data=image_bytes,
            mime_type=mime_type
        )

    def get_ai_response(
        self, 
        user_message: str, 
        context: Optional[list] = None,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 10000
    ) -> str:
        """
        Get AI response for user message
        
        Args:
            user_message: The message from the user
            context: Optional conversation context
            model: Model to use (defaults to instance default_model)
            temperature: Sampling temperature
            max_tokens: Maximum tokens in response
            
        Returns:
            str: AI response
        """
        try:
            if self.model_type == "gemini":
                contents = []
                if context:
                    for m in context:
                        role = "model" if m.get("role") == "assistant" else m.get("role")
                        contents.append({
                            "role": role, 
                            "parts": [{"text": m.get("content", "")}]
                        })
                
                contents.append({
                    "role": "user", 
                    "parts": [{"text": user_message}]
                })
                
                response = self.client.models.generate_content(
                    model=model or self.fast_model,
                    contents=contents
                )
                return response.text
            
            # OpenAI/DeepSeek logic
            messages = []
            if context:
                messages.extend(context)
            messages.append({"role": "user", "content": user_message})
            
            response = self.client.chat.completions.create(
                model=model or self.fast_model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens
            )
            return response.choices[0].message.content
            
        except Exception as e:
            return f"I met some problems: {str(e)}"

    def understand_image(
        self,
        image_path: str,
        user_prompt: str,
        context: Optional[list] = None
    ) -> str:
        """
        Understand and comment on a single image (legacy method, calls understand_images)
        
        Args:
            image_path: Path to the image file
            user_prompt: The user's question or request about the image
            context: Optional conversation context
            
        Returns:
            str: AI's response about the image
        """
        return self.understand_images(
            image_paths=[image_path],
            user_prompt=user_prompt,
            context=context
        )

    def understand_images(
        self,
        image_paths: Union[str, List[str]],
        user_prompt: str,
        context: Optional[list] = None
    ) -> str:
        """
        Understand and comment on one or more images using Gemini's image understanding capability
        
        Args:
            image_paths: Path(s) to image file(s) - can be a single string or a list
            user_prompt: The user's question or request about the image(s)
            context: Optional conversation context
            
        Returns:
            str: AI's response about the image(s)
        """
        try:
            # Normalize to list
            if isinstance(image_paths, str):
                image_paths = [image_paths]
            
            num_images = len(image_paths)
            print(f"理解 {num_images} 张图片")
            
            # Build contents - images first, then text
            contents = []
            
            for i, image_path in enumerate(image_paths):
                image_part = self._load_image_as_part(image_path)
                contents.append(image_part)
                print(f"已加载图片 {i+1}: {image_path}")
            
            # Add text prompt
            contents.append(user_prompt)
            
            response = self.client.models.generate_content(
                model=self.image_understand_model,
                contents=contents
            )
            
            return response.text
            
        except Exception as e:
            return f"图片理解时遇到问题: {str(e)}"

    def reflect_on_generated_image(
        self,
        generated_image_path: str,
        original_prompt: str,
        reference_image_paths: Optional[List[str]] = None
    ) -> dict:
        """
        反思生成的图片质量，评估是否满足用户需求
        
        Args:
            generated_image_path: 生成的图片路径
            original_prompt: 用户原始的生成请求
            reference_image_paths: 参考图片路径列表（如果有的话）
            
        Returns:
            dict: {
                'is_satisfactory': bool,  # 是否满意
                'score': int,  # 1-10分
                'analysis': str,  # 分析说明
                'strengths': list,  # 做得好的地方
                'improved_prompt': str,  # 改进后的提示词（如果不满意）
                'issues': list  # 发现的问题列表
            }
        """
        try:
            contents = []
            
            # 添加生成的图片
            generated_image_part = self._load_image_as_part(generated_image_path)
            contents.append(generated_image_part)
            
            # 如果有参考图片，也添加进去
            if reference_image_paths:
                for ref_path in reference_image_paths:
                    ref_part = self._load_image_as_part(ref_path)
                    contents.append(ref_part)
            
            # 构建反思提示
            ref_hint = ""
            if reference_image_paths:
                ref_hint = f"（用户还提供了 {len(reference_image_paths)} 张参考图片，后面的图片是参考图片）"
            
            reflection_prompt = f"""你是一名严格但务实的资深平面设计师，正在评审 AI 生成的设计图片。请评估第一张图片（AI生成的图片）是否精准满足用户的真实需求，而不是按无关的商业素材标准过度挑剔。

用户的原始请求: {original_prompt}
{ref_hint}

先判断用户的任务类型和核心交付物：
- 如果用户只是说“画个西瓜/生成一只猫”这类简单绘图需求，核心是主体正确、画面可用、没有明显崩坏；不要因为轻微风格偏好或局部小瑕疵判失败。
- 如果用户要求海报、Logo、UI、包装、广告图等设计交付物，再按版式、文字、品牌感、可交付程度严格评估。
- 如果用户提供参考图，重点检查是否尊重参考图和用户的修改指令。

请从以下维度评估生成的图片，权重必须服从用户原始请求:
1. 需求匹配: 是否准确完成用户明确要求的主体、动作、风格、文字、比例、参考图修改点？
2. 明显错误: 是否存在主体缺失、数量错误、文字乱码、结构崩坏、严重变形、违背用户要求等问题？
3. 画面质量: 构图、色彩、光线和清晰度是否足够支撑用户当前任务？
4. 细节瑕疵: 只记录轻微问题，不要把不影响需求完成的小瑕疵当成失败理由。

请用以下JSON格式回复（不要包含任何其他内容，只输出JSON）:
{{
    "score": <1-10的整数分数>,
    "is_satisfactory": <true或false，7分及以上必须为true，低于7分才为false>,
    "strengths": ["做得好的地方1", "做得好的地方2", ...],
    "issues": ["问题1", "问题2", ...],
    "analysis": "简短的总体分析，2-3句话；如果通过，要说明好在哪里；如果不通过，要说明主要问题在哪里",
    "improved_prompt": "如果不满意，给出改进后的图片生成提示词；如果满意则为空字符串"
}}

注意：
- 先精确复述/理解用户需求，再据此打分；不要引入用户没要求的评价维度作为失败理由
- 如果用户原本的请求就很简单，只要主体正确且无明显崩坏，通常应给7分及以上
- is_satisfactory 为 true 时，issues 应该为空或只包含轻微问题，strengths 必须说明具体优点，不能只写“不错”
- is_satisfactory 为 false 时，issues 必须指出具体没有满足原始需求的位置或原因，不能只写“质量一般”
- improved_prompt 应该紧扣用户原始需求，只补充必要的构图、风格、细节和修正点，不要改变主题或额外添加用户没要的元素
- 只输出JSON，不要有任何前缀或后缀文字"""

            contents.append(reflection_prompt)
            
            # 调用模型进行反思
            response = self.client.models.generate_content(
                model=self.judge_model,
                contents=contents
            )
            
            # 解析响应
            response_text = response.text.strip()
            
            # 尝试提取JSON（处理可能的markdown代码块）
            if response_text.startswith("```"):
                # 移除markdown代码块标记
                lines = response_text.split("\n")
                json_lines = []
                in_json = False
                for line in lines:
                    if line.startswith("```"):
                        in_json = not in_json
                        continue
                    if in_json or (not line.startswith("```")):
                        json_lines.append(line)
                response_text = "\n".join(json_lines).strip()
            
            import json
            result = json.loads(response_text)
            
            # 确保返回的字段完整
            return {
                'is_satisfactory': result.get('is_satisfactory', True),
                'score': result.get('score', 7),
                'analysis': result.get('analysis', ''),
                'strengths': result.get('strengths', []),
                'improved_prompt': result.get('improved_prompt', ''),
                'issues': result.get('issues', [])
            }
            
        except Exception as e:
            print(f"反思图片时出错: {e}")
            # 出错时默认返回满意，避免无限循环
            return {
                'is_satisfactory': True,
                'score': 7,
                'analysis': f'反思过程出错: {str(e)}',
                'strengths': [],
                'improved_prompt': '',
                'issues': []
            }

    def generate_image_with_reference(
        self,
        image_path: str,
        user_prompt: str,
        use_pro: bool = False
    ) -> Tuple[str, Optional[bytes]]:
        """
        Generate a new image based on a single reference image (legacy method, calls generate_image_with_references)
        
        Args:
            image_path: Path to the reference image file
            user_prompt: The user's instruction for image generation/editing
            use_pro: Whether to use the Pro model
            
        Returns:
            tuple: (text_response, image_bytes or None)
        """
        return self.generate_image_with_references(
            image_paths=[image_path],
            user_prompt=user_prompt,
            use_pro=use_pro
        )

    def generate_image_with_references(
        self,
        image_paths: Union[str, List[str]],
        user_prompt: str,
        use_pro: bool = False,
        preserve_reference_resolution: bool = False,
        output_aspect_ratio: str | None = None,
        output_image_size: str | None = None,
    ) -> Tuple[str, Optional[bytes]]:
        """
        Generate a new image based on one or more reference images and text prompt.
        This is used for image editing, style transfer, merging, or creating variations.
        
        Args:
            image_paths: Path(s) to reference image file(s) - can be a single string or a list
            user_prompt: The user's instruction for image generation/editing
            use_pro: Whether to use the Pro model (gemini-3-pro-image-preview)
            preserve_reference_resolution: Keep a single reference image's original canvas.
            output_aspect_ratio: Explicit model output aspect ratio such as "4:3".
            output_image_size: Optional model output size tier such as "1K", "2K", or "4K".
            
        Returns:
            tuple: (text_response, image_bytes or None)
        """
        try:
            # Debug: print raw input
            print(f"[DEBUG] generate_image_with_references 收到参数:")
            print(f"[DEBUG]   image_paths type: {type(image_paths)}")
            print(f"[DEBUG]   image_paths value: {image_paths}")
            
            # Normalize to list
            if isinstance(image_paths, str):
                image_paths = [image_paths]
            
            num_images = len(image_paths)
            print(f"基于 {num_images} 张图片生成新图片")
            
            # Validate each path
            for i, path in enumerate(image_paths):
                print(f"[DEBUG]   path[{i}] type: {type(path)}, value: {path}")
                if not isinstance(path, str):
                    raise ValueError(f"image_paths[{i}] 应该是字符串，但收到 {type(path)}: {path}")
            
            model = self.image_gen_pro_model if use_pro else self.image_gen_model
            print(f"使用模型: {model}")

            source_size = None
            if preserve_reference_resolution and len(image_paths) == 1 and not output_aspect_ratio:
                source_size = get_display_size(image_paths[0])
                user_prompt = (
                    f"{user_prompt}\n\n"
                    "Strict editing constraint: keep the original canvas, framing, "
                    f"aspect ratio, and pixel dimensions ({source_size[0]}x{source_size[1]}). "
                    "Do not crop, extend, rotate, or resize the image. Change only the "
                    "elements explicitly requested by the user."
                )
            
            # Build contents: prompt first, then images
            # According to docs: contents=[prompt, image] for text-and-image-to-image
            contents = [user_prompt]
            
            for i, image_path in enumerate(image_paths):
                image_part = self._load_image_as_part(image_path)
                contents.append(image_part)
                print(f"已加载参考图片 {i+1}: {image_path}")
            
            config = None
            if output_aspect_ratio:
                image_size = output_image_size or "1K"
                config = types.GenerateContentConfig(
                    response_modalities=["TEXT", "IMAGE"],
                    image_config=types.ImageConfig(
                        aspect_ratio=output_aspect_ratio,
                        image_size=image_size,
                    ),
                )
                print(
                    "图片输出画幅约束: "
                    f"比例={output_aspect_ratio}, 模型尺寸={image_size}"
                )
            elif source_size:
                source_width, source_height = source_size
                aspect_ratio = closest_supported_aspect_ratio(
                    source_width,
                    source_height,
                    allow_extreme="3.1-flash-image" in model,
                )
                image_size = recommended_image_size(source_width, source_height)
                config = types.GenerateContentConfig(
                    response_modalities=["TEXT", "IMAGE"],
                    image_config=types.ImageConfig(
                        aspect_ratio=aspect_ratio,
                        image_size=image_size,
                    ),
                )
                print(
                    "编辑图片输出约束: "
                    f"原图={source_width}x{source_height}, "
                    f"比例={aspect_ratio}, 模型尺寸={image_size}"
                )

            # Generate with reference images
            response = self.client.models.generate_content(
                model=model,
                contents=contents,
                config=config,
            )
            
            # Process response - may contain text and/or image
            text_response = ""
            generated_image = None
            
            for part in response.parts:
                if part.text is not None:
                    text_response = part.text
                elif part.inline_data is not None:
                    # Get the image data
                    generated_image = part.inline_data.data
                    if isinstance(generated_image, str):
                        # If it's base64 encoded, decode it
                        generated_image = base64.b64decode(generated_image)
            
            return text_response, generated_image
            
        except Exception as e:
            return f"基于图片生成时遇到问题: {str(e)}", None

    def generate_image(
        self,
        prompt: str,
        use_pro: bool = False
    ) -> Tuple[str, Optional[bytes]]:
        """
        Generate a new image from text prompt only (no reference image)
        
        Args:
            prompt: The text description of the image to generate
            use_pro: Whether to use the Pro model (gemini-3-pro-image-preview)
            
        Returns:
            tuple: (text_response, image_bytes or None)
        """
        try:
            model = self.image_gen_pro_model if use_pro else self.image_gen_model
            print(f"使用模型: {model}")
            
            response = self.client.models.generate_content(
                model=model,
                contents=[prompt]
            )
            
            text_response = ""
            generated_image = None
            
            for part in response.parts:
                if part.text is not None:
                    text_response = part.text
                elif part.inline_data is not None:
                    generated_image = part.inline_data.data
                    if isinstance(generated_image, str):
                        generated_image = base64.b64decode(generated_image)
            
            return text_response, generated_image
            
        except Exception as e:
            return f"图片生成时遇到问题: {str(e)}", None
