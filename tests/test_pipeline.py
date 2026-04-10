"""Tests for the image processing pipeline."""

import io
import json

import piexif
from PIL import Image

from mcp_bildsprache.pipeline import process_image
from mcp_bildsprache.types import ProviderResult


def _make_test_image(width: int = 800, height: int = 600, fmt: str = "PNG", mode: str = "RGB") -> bytes:
    """Create a test image as bytes."""
    img = Image.new(mode, (width, height), color=(100, 150, 200) if mode == "RGB" else None)
    buf = io.BytesIO()
    if fmt == "JPEG" and mode != "RGB":
        img = img.convert("RGB")
    img.save(buf, format=fmt)
    return buf.getvalue()


def _make_provider_result(width: int = 800, height: int = 600, fmt: str = "PNG") -> ProviderResult:
    return ProviderResult(
        image_data=_make_test_image(width, height, fmt),
        mime_type=f"image/{fmt.lower()}",
        model="test-model",
        cost_estimate="$0.00",
    )


class TestResizeCrop:
    def test_exact_dimensions_produced(self):
        result_bytes = process_image(
            _make_provider_result(800, 600),
            target_width=1200,
            target_height=630,
            prompt="test prompt",
        )
        img = Image.open(io.BytesIO(result_bytes))
        assert img.size == (1200, 630)

    def test_downscale_exact(self):
        result_bytes = process_image(
            _make_provider_result(2000, 2000),
            target_width=512,
            target_height=512,
            prompt="test prompt",
        )
        img = Image.open(io.BytesIO(result_bytes))
        assert img.size == (512, 512)

    def test_aspect_ratio_change_crops(self):
        """Square input → wide output should crop top/bottom."""
        result_bytes = process_image(
            _make_provider_result(1024, 1024),
            target_width=1600,
            target_height=900,
            prompt="test prompt",
        )
        img = Image.open(io.BytesIO(result_bytes))
        assert img.size == (1600, 900)

    def test_wide_to_tall_crops(self):
        """Wide input → tall output should crop left/right."""
        result_bytes = process_image(
            _make_provider_result(1600, 900),
            target_width=1080,
            target_height=1920,
            prompt="test prompt",
        )
        img = Image.open(io.BytesIO(result_bytes))
        assert img.size == (1080, 1920)


class TestWebPConversion:
    def test_output_is_webp(self):
        result_bytes = process_image(
            _make_provider_result(800, 600, "PNG"),
            target_width=800,
            target_height=600,
            prompt="test prompt",
        )
        img = Image.open(io.BytesIO(result_bytes))
        assert img.format == "WEBP"

    def test_jpeg_input_produces_webp(self):
        result_bytes = process_image(
            _make_provider_result(800, 600, "JPEG"),
            target_width=800,
            target_height=600,
            prompt="test prompt",
        )
        img = Image.open(io.BytesIO(result_bytes))
        assert img.format == "WEBP"


class TestExifMetadata:
    def test_exif_present_in_webp(self):
        result_bytes = process_image(
            _make_provider_result(),
            target_width=800,
            target_height=600,
            prompt="a beautiful sunset over Berlin",
        )
        img = Image.open(io.BytesIO(result_bytes))
        exif_data = img.info.get("exif")
        assert exif_data is not None

    def test_exif_contains_software_tag(self):
        result_bytes = process_image(
            _make_provider_result(),
            target_width=800,
            target_height=600,
            prompt="test prompt",
        )
        exif_dict = piexif.load(result_bytes)
        software = exif_dict["0th"].get(piexif.ImageIFD.Software)
        assert software == b"Bildsprache AI"

    def test_exif_contains_prompt_hash_not_prompt(self):
        prompt = "a secret prompt that should not appear in metadata"
        result_bytes = process_image(
            _make_provider_result(),
            target_width=800,
            target_height=600,
            prompt=prompt,
        )
        exif_dict = piexif.load(result_bytes)
        user_comment = exif_dict["Exif"].get(piexif.ExifIFD.UserComment, b"")
        # Strip ASCII prefix
        comment_str = user_comment[8:].decode("ascii", errors="replace")
        comment_data = json.loads(comment_str)

        assert "prompt_hash" in comment_data
        assert len(comment_data["prompt_hash"]) == 64  # SHA-256 hex
        assert prompt not in comment_str

    def test_exif_contains_model_and_timestamp(self):
        result_bytes = process_image(
            _make_provider_result(),
            target_width=800,
            target_height=600,
            prompt="test",
        )
        exif_dict = piexif.load(result_bytes)
        user_comment = exif_dict["Exif"][piexif.ExifIFD.UserComment]
        comment_data = json.loads(user_comment[8:].decode("ascii", errors="replace"))

        assert comment_data["model"] == "test-model"
        assert "generated_at" in comment_data
        assert comment_data["generator"] == "Bildsprache AI"


class TestInputModeConversion:
    def test_rgba_input_converted_to_rgb(self):
        """RGBA PNG input should be handled without error."""
        rgba_buf = io.BytesIO()
        Image.new("RGBA", (800, 600), color=(100, 150, 200, 128)).save(rgba_buf, format="PNG")
        pr = ProviderResult(
            image_data=rgba_buf.getvalue(),
            mime_type="image/png",
            model="test-model",
            cost_estimate="$0.00",
        )
        result_bytes = process_image(pr, target_width=400, target_height=300, prompt="test")
        img = Image.open(io.BytesIO(result_bytes))
        assert img.size == (400, 300)
        assert img.format == "WEBP"

    def test_palette_mode_input_handled(self):
        """Palette-mode ('P') PNG should be converted and processed."""
        p_buf = io.BytesIO()
        img = Image.new("P", (800, 600))
        img.save(p_buf, format="PNG")
        pr = ProviderResult(
            image_data=p_buf.getvalue(),
            mime_type="image/png",
            model="test-model",
            cost_estimate="$0.00",
        )
        result_bytes = process_image(pr, target_width=400, target_height=300, prompt="test")
        out = Image.open(io.BytesIO(result_bytes))
        assert out.size == (400, 300)


class TestExifBrandContext:
    def test_exif_brand_context_included_when_passed(self):
        result_bytes = process_image(
            _make_provider_result(),
            target_width=800,
            target_height=600,
            prompt="test",
            brand_context="@casey.berlin",
        )
        exif_dict = piexif.load(result_bytes)
        user_comment = exif_dict["Exif"][piexif.ExifIFD.UserComment]
        comment_data = json.loads(user_comment[8:].decode("ascii", errors="replace"))
        assert comment_data["brand_context"] == "@casey.berlin"

    def test_exif_brand_context_null_when_not_passed(self):
        result_bytes = process_image(
            _make_provider_result(),
            target_width=800,
            target_height=600,
            prompt="test",
        )
        exif_dict = piexif.load(result_bytes)
        user_comment = exif_dict["Exif"][piexif.ExifIFD.UserComment]
        comment_data = json.loads(user_comment[8:].decode("ascii", errors="replace"))
        assert comment_data["brand_context"] is None
