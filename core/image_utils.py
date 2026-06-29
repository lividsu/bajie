"""Utilities for generated image sizing, metadata, and source alignment."""

from io import BytesIO
import math
import os
from pathlib import Path
import re
from typing import Any, Tuple

from PIL import Image, ImageOps


STANDARD_ASPECT_RATIOS = (
    ("1:1", 1 / 1),
    ("2:3", 2 / 3),
    ("3:2", 3 / 2),
    ("3:4", 3 / 4),
    ("4:3", 4 / 3),
    ("4:5", 4 / 5),
    ("5:4", 5 / 4),
    ("9:16", 9 / 16),
    ("16:9", 16 / 9),
    ("21:9", 21 / 9),
)

EXTREME_ASPECT_RATIOS = (
    ("1:4", 1 / 4),
    ("1:8", 1 / 8),
    ("4:1", 4 / 1),
    ("8:1", 8 / 1),
)

USD_TO_CNY_RATE = 6.80
IMAGE_OUTPUT_PRICING_USD = {
    "default": {
        "1K": 0.067,
        "2K": 0.101,
        "4K": 0.151,
    },
    "pro": {
        "1K": 0.134,
        "2K": 0.134,
        "4K": 0.240,
    },
}

ASPECT_RATIO_PATTERN = re.compile(r"(?<!\d)(\d{1,2})\s*(?:[:：/]|比)\s*(\d{1,2})(?!\d)")


def get_display_size(image_path: str) -> Tuple[int, int]:
    """Return image dimensions after applying EXIF orientation."""
    with Image.open(image_path) as image:
        return ImageOps.exif_transpose(image).size


def closest_supported_aspect_ratio(
    width: int,
    height: int,
    allow_extreme: bool = False,
) -> str:
    """Choose the model aspect ratio closest to the source image."""
    if width <= 0 or height <= 0:
        raise ValueError("Image dimensions must be positive")

    actual_ratio = width / height
    supported_ratios = STANDARD_ASPECT_RATIOS
    if allow_extreme:
        supported_ratios += EXTREME_ASPECT_RATIOS
    return min(
        supported_ratios,
        key=lambda item: abs(math.log(actual_ratio / item[1])),
    )[0]


def requested_aspect_ratio(message: str, allow_extreme: bool = False) -> str | None:
    """Return the closest supported aspect ratio explicitly requested in text."""
    if not message:
        return None

    match = ASPECT_RATIO_PATTERN.search(message)
    if not match:
        return None

    width = int(match.group(1))
    height = int(match.group(2))
    return closest_supported_aspect_ratio(width, height, allow_extreme=allow_extreme)


def recommended_image_size(width: int, height: int) -> str:
    """Request enough model resolution to avoid unnecessary upscaling."""
    longest_edge = max(width, height)
    if longest_edge <= 1024:
        return "1K"
    if longest_edge <= 2048:
        return "2K"
    return "4K"


def format_file_size(byte_count: int) -> str:
    """Return a compact human-readable file size."""
    if byte_count < 1024:
        return f"{byte_count} B"
    if byte_count < 1024 * 1024:
        return f"{byte_count / 1024:.1f} KB"
    return f"{byte_count / (1024 * 1024):.2f} MB"


def estimate_image_generation_cost_cny(
    quality_tier: str,
    use_pro: bool = False,
    usd_to_cny_rate: float = USD_TO_CNY_RATE,
) -> float | None:
    """Estimate output image cost in CNY from the model resolution tier."""
    pricing_key = "pro" if use_pro else "default"
    usd_cost = IMAGE_OUTPUT_PRICING_USD.get(pricing_key, {}).get(quality_tier)
    if usd_cost is None:
        return None
    return usd_cost * usd_to_cny_rate


def image_generation_metadata(
    image_path: str,
    model: str,
    use_pro: bool = False,
    usd_to_cny_rate: float = USD_TO_CNY_RATE,
) -> dict[str, Any]:
    """Read saved image metadata and add model/cost estimates."""
    path = Path(image_path)
    width, height = get_display_size(str(path))
    quality_tier = recommended_image_size(width, height)
    return {
        "resolution": f"{width} x {height} px",
        "file_size": format_file_size(os.path.getsize(path)),
        "model": model,
        "aspect_ratio": closest_supported_aspect_ratio(width, height, allow_extreme=True),
        "quality": quality_tier,
        "estimated_cost_cny": estimate_image_generation_cost_cny(
            quality_tier,
            use_pro=use_pro,
            usd_to_cny_rate=usd_to_cny_rate,
        ),
    }


def format_image_generation_info(
    image_path: str,
    model: str,
    use_pro: bool = False,
    usd_to_cny_rate: float = USD_TO_CNY_RATE,
    title: str = "图片信息",
) -> str:
    """Format generated image metadata for the user-facing reply."""
    metadata = image_generation_metadata(
        image_path,
        model=model,
        use_pro=use_pro,
        usd_to_cny_rate=usd_to_cny_rate,
    )
    cost = metadata["estimated_cost_cny"]
    cost_text = "未知" if cost is None else f"约 {cost:.2f} 元，人民币估算"
    return (
        f"{title}\n"
        f"- 实际分辨率：{metadata['resolution']}\n"
        f"- 文件大小：{metadata['file_size']}\n"
        f"- 调用模型：{metadata['model']}\n"
        f"- 比例：{metadata['aspect_ratio']}\n"
        f"- 输出质量：{metadata['quality']}\n"
        f"- 预计费用：{cost_text}"
    )


def restore_source_dimensions(
    image_bytes: bytes,
    source_image_path: str,
) -> tuple[bytes, Tuple[int, int], Tuple[int, int]]:
    """Resize generated image bytes to the source image's exact dimensions."""
    source_path = Path(source_image_path)
    if not source_path.is_file():
        raise FileNotFoundError(f"Source image does not exist: {source_image_path}")

    with Image.open(source_path) as source:
        source = ImageOps.exif_transpose(source)
        target_size = source.size
        source_dpi = source.info.get("dpi")

    with Image.open(BytesIO(image_bytes)) as generated:
        generated = ImageOps.exif_transpose(generated)
        generated.load()
        generated_size = generated.size

        if generated_size != target_size:
            generated = generated.resize(target_size, Image.Resampling.LANCZOS)

        output = BytesIO()
        save_options = {"format": "PNG"}
        if source_dpi:
            save_options["dpi"] = source_dpi
        generated.save(output, **save_options)

    return output.getvalue(), target_size, generated_size
