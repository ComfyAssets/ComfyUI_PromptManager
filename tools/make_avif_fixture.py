"""Write tests/fixtures/media/comfyui_avif.avif with ComfyUI's own AVIF writer.

Run from a ComfyUI checkout with ComfyUI's Python (torch, and PyAV built with
SVT-AV1), because the fixture must be what ``SaveImageAdvanced`` writes, not
an AVIF some other encoder made:

    cd /path/to/ComfyUI
    python custom_nodes/ComfyUI-PromptManager/tools/make_avif_fixture.py

``comfy_extras.nodes_images._save_avif`` encodes the pixels through PyAV and
then ``inject_avif_metadata`` writes the same EXIF item ComfyUI writes:
``prompt:{json}`` at tag 0x0110, then ``workflow:{json}`` and every other key
from 0x010F down. ``utils/exif_text`` reads those tags back (#652).

The image is a 96x96 sRGB gradient at CRF 18: small enough to keep in the
repository and large enough that the EXIF item dominates the file.
"""

import json
import os
import sys

# Importing comfy_extras requires ComfyUI's own root on sys.path; the script
# is run from that root (see the docstring).
sys.path.insert(0, os.getcwd())

import torch  # noqa: E402

from comfy_extras.nodes_images import _save_avif  # noqa: E402

# A PromptManager run: the positive prompt is the node's own text (the shape
# tests/test_image_linking.py's graph uses), so both the scan and the monitor
# link the image to that stored prompt instead of calling it a foreign output.
PROMPT = {
    "1": {
        "class_type": "PromptManager",
        "inputs": {"text": "a small avif fixture"},
    },
    "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "bad"}},
    "3": {
        "class_type": "KSampler",
        "inputs": {"positive": ["1", 0], "negative": ["2", 0]},
    },
}
WORKFLOW = {
    "nodes": [
        {"id": 1, "type": "PromptManager", "inputs": {"text": "a small avif fixture"}},
        {"id": 2, "type": "CLIPTextEncode", "inputs": {"text": "bad"}},
        {"id": 3, "type": "KSampler", "inputs": {}},
    ],
    "links": [
        [1, 0, 3, 0, "CONDITIONING"],
        [2, 0, 3, 1, "CONDITIONING"],
    ],
}
OUTPUT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "tests",
    "fixtures",
    "media",
    "comfyui_avif.avif",
)


def main():
    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    # (frames, height, width, channels), floats in 0..1 as ComfyUI's IMAGE is.
    ramp = torch.linspace(0.0, 1.0, 96).view(1, 1, 96, 1).expand(1, 96, 96, 3)
    _save_avif(
        ramp.contiguous(),
        OUTPUT,
        "8-bit YUV420",
        "sRGB",
        18,
        metadata={"prompt": PROMPT, "workflow": WORKFLOW},
    )
    print(f"{OUTPUT}: {os.path.getsize(OUTPUT)} bytes")
    print(json.dumps({"prompt_keys": list(PROMPT), "workflow_keys": list(WORKFLOW)}))


if __name__ == "__main__":
    main()
