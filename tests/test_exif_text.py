"""ComfyUI's EXIF records: what WebP, AVIF and JPEG carry instead of text.

ComfyUI's SaveAnimatedWEBP writes ``prompt:{json}`` to EXIF tag 0x0110, then
``workflow:{json}`` and every other ``extra_pnginfo`` key to 0x010F and down;
the AVIF writer uses that layout too. Pillow exposes them only through
``getexif()``, so a reader that looks at ``image.info`` or the text chunks
finds nothing. These tests write real files in that layout.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image, features

from utils.exif_text import exif_text

PROMPT = {"1": {"class_type": "KSampler", "inputs": {"seed": 7}}}
WORKFLOW = {"nodes": [{"type": "CLIPTextEncode", "inputs": {"text": "cat"}}]}
PAIRS = [{"positive": "cat", "negative": "dog"}]


def comfyui_exif(prompt=PROMPT, extra=None):
    """The tags ComfyUI writes: the prompt on 0x0110, extra_pnginfo from 0x010F."""
    exif = Image.Exif()
    if prompt is not None:
        exif[0x0110] = f"prompt:{json.dumps(prompt)}"
    tag = 0x010F
    for key, value in ({"workflow": WORKFLOW} if extra is None else extra).items():
        exif[tag] = f"{key}:{json.dumps(value)}"
        tag -= 1
    return exif


class ExifTextCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = folder.name

    def write(self, name, image_format, exif, **options):
        path = os.path.join(self.folder, name)
        Image.new("RGB", (8, 8), "red").save(path, image_format, exif=exif, **options)
        return path

    def read(self, path):
        with Image.open(path) as image:
            return exif_text(image)


class TestComfyuiRecords(ExifTextCase):
    def test_reads_the_records_of_a_webp(self):
        path = self.write("a.webp", "WEBP", comfyui_exif())

        self.assertEqual(
            self.read(path),
            {"prompt": json.dumps(PROMPT), "workflow": json.dumps(WORKFLOW)},
        )

    def test_reads_an_animated_webp(self):
        # SaveAnimatedWEBP writes the first frame's EXIF and appends the rest.
        frames = [Image.new("RGB", (8, 8), color) for color in ("red", "blue")]
        path = os.path.join(self.folder, "anim.webp")
        frames[0].save(
            path,
            "WEBP",
            save_all=True,
            append_images=frames[1:],
            duration=100,
            exif=comfyui_exif(),
        )

        self.assertEqual(self.read(path)["workflow"], json.dumps(WORKFLOW))

    def test_reads_every_extra_pnginfo_key_and_not_only_the_workflow(self):
        exif = comfyui_exif(
            extra={"workflow": WORKFLOW, "comfyassets_prompt_pairs": PAIRS}
        )
        path = self.write("pairs.webp", "WEBP", exif)

        self.assertEqual(self.read(path)["comfyassets_prompt_pairs"], json.dumps(PAIRS))

    def test_reads_a_jpeg_in_the_same_layout(self):
        # Not a core saver, but a third-party one reusing the convention.
        path = self.write("a.jpg", "JPEG", comfyui_exif())

        self.assertEqual(self.read(path)["prompt"], json.dumps(PROMPT))

    @unittest.skipUnless(features.check("avif"), "Pillow has no AVIF support")
    def test_reads_an_avif(self):
        path = self.write("a.avif", "AVIF", comfyui_exif())

        self.assertEqual(self.read(path)["workflow"], json.dumps(WORKFLOW))


class TestOtherExif(ExifTextCase):
    def test_a_camera_exif_is_not_a_comfyui_record(self):
        exif = Image.Exif()
        exif[0x010F] = "Canon"  # Make
        exif[0x0110] = "Canon EOS R5"  # Model
        exif[0x0131] = "GIMP 2.10.38"  # Software
        exif[0x0132] = "2024:01:02 03:04:05"  # DateTime
        path = self.write("camera.jpg", "JPEG", exif)

        self.assertEqual(self.read(path), {})

    def test_a_value_that_is_not_json_is_not_a_record(self):
        exif = Image.Exif()
        exif[0x010E] = "note:not json"
        exif[0x010D] = "make:Canon"
        path = self.write("notes.webp", "WEBP", exif)

        self.assertEqual(self.read(path), {})

    def test_a_png_answers_empty_its_records_are_text_chunks(self):
        from PIL.PngImagePlugin import PngInfo

        info = PngInfo()
        info.add_text("workflow", json.dumps(WORKFLOW))
        path = os.path.join(self.folder, "a.png")
        Image.new("RGB", (8, 8)).save(path, pnginfo=info)

        self.assertEqual(self.read(path), {})


if __name__ == "__main__":
    unittest.main()
