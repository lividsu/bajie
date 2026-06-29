from io import BytesIO
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from core.image_utils import (
    closest_supported_aspect_ratio,
    estimate_image_generation_cost_cny,
    format_image_generation_info,
    recommended_image_size,
    requested_aspect_ratio,
    restore_source_dimensions,
)


def _image_bytes(size):
    image = Image.new("RGB", size, "white")
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class ImageUtilsTests(unittest.TestCase):
    def test_selects_aspect_ratio_and_resolution_tier(self):
        self.assertEqual(closest_supported_aspect_ratio(1920, 1080), "16:9")
        self.assertEqual(closest_supported_aspect_ratio(1080, 1920), "9:16")
        self.assertEqual(
            closest_supported_aspect_ratio(4096, 512, allow_extreme=True),
            "8:1",
        )
        self.assertEqual(closest_supported_aspect_ratio(4096, 512), "21:9")
        self.assertEqual(recommended_image_size(800, 600), "1K")
        self.assertEqual(recommended_image_size(1600, 1200), "2K")
        self.assertEqual(recommended_image_size(3000, 2000), "4K")
        self.assertAlmostEqual(estimate_image_generation_cost_cny("1K"), 0.4556)
        self.assertAlmostEqual(estimate_image_generation_cost_cny("1K", use_pro=True), 0.9112)

    def test_formats_generated_image_info_with_cny_estimate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "generated.png"
            Image.new("RGB", (800, 600), "white").save(image_path)

            info = format_image_generation_info(
                str(image_path),
                model="gemini-3.1-flash-image-preview",
            )

            self.assertIn("图片信息", info)
            self.assertIn("实际分辨率：800 x 600 px", info)
            self.assertIn("调用模型：gemini-3.1-flash-image-preview", info)
            self.assertIn("比例：4:3", info)
            self.assertIn("输出质量：1K", info)
            self.assertIn("预计费用：约 0.46 元，人民币估算", info)

    def test_extracts_requested_aspect_ratio(self):
        self.assertEqual(requested_aspect_ratio("改成适合手机端的4：3比例"), "4:3")
        self.assertEqual(requested_aspect_ratio("请重构为 3:4 竖版"), "3:4")
        self.assertEqual(requested_aspect_ratio("做成 8比6 的画幅"), "4:3")
        self.assertIsNone(requested_aspect_ratio("只帮我压缩一下图片"))

    def test_restores_exact_source_dimensions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "source.png"
            Image.new("RGB", (1234, 567), "black").save(source_path, dpi=(300, 300))

            restored, target_size, generated_size = restore_source_dimensions(
                _image_bytes((1024, 1024)),
                str(source_path),
            )

            self.assertEqual(target_size, (1234, 567))
            self.assertEqual(generated_size, (1024, 1024))
            with Image.open(BytesIO(restored)) as result:
                self.assertEqual(result.size, (1234, 567))
                self.assertEqual(result.format, "PNG")
                self.assertAlmostEqual(result.info["dpi"][0], 300, delta=1)

    def test_keeps_dimensions_when_model_already_matches(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "source.png"
            Image.new("RGB", (640, 480), "black").save(source_path)

            restored, target_size, generated_size = restore_source_dimensions(
                _image_bytes((640, 480)),
                str(source_path),
            )

            self.assertEqual(target_size, generated_size)
            with Image.open(BytesIO(restored)) as result:
                self.assertEqual(result.size, (640, 480))


if __name__ == "__main__":
    unittest.main()
