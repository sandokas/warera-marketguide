"""Local image validation and portable display data; no network or database access."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import re
from pathlib import Path
import xml.etree.ElementTree as ET

from PIL import Image


OFFICIAL_ASSETS = Path(__file__).parent / "assets" / "official"


def normalize_image(content: bytes, mime: str) -> tuple[bytes, str, int, int]:
    if mime == "image/svg+xml":
        if b"<!" in content:
            raise ValueError("SVG declarations are not supported")
        root = ET.fromstring(content)
        if root.tag.split("}")[-1] != "svg":
            raise ValueError("Expected SVG root")
        allowed = {"svg", "g", "path", "rect", "circle", "ellipse", "polygon", "polyline",
                   "line", "defs", "clipPath", "mask", "linearGradient", "radialGradient", "stop", "use"}
        for node in root.iter():
            if node.tag.split("}")[-1] not in allowed:
                raise ValueError("Unsupported SVG element")
            for key, value in node.attrib.items():
                if key.split("}")[-1].lower().startswith("on") or (key.endswith("href") and not value.startswith("#")):
                    raise ValueError("External/active SVG content")
                if any(not target.startswith("#") for target in re.findall(r"url\((.*?)\)", value, re.I)):
                    raise ValueError("External SVG reference")
        viewbox = root.get("viewBox", "").split()
        width = float(root.get("width") or viewbox[2])
        height = float(root.get("height") or viewbox[3])
        if not 0 < width <= 4096 or not 0 < height <= 4096:
            raise ValueError("Invalid image dimensions")
        return content, mime, int(width), int(height)
    if mime not in {"image/png", "image/jpeg", "image/gif", "image/webp"}:
        raise ValueError("Unsupported image content type")
    with Image.open(io.BytesIO(content)) as source:
        if not 0 < source.width <= 4096 or not 0 < source.height <= 4096:
            raise ValueError("Invalid image dimensions")
        source.seek(0)
        output = io.BytesIO()
        source.convert("RGBA").save(output, format="PNG")
        return output.getvalue(), "image/png", source.width, source.height


def asset_data_uri(asset: dict | None) -> str | None:
    if not asset or not asset.get("local_path"):
        return None
    try:
        path = Path(asset["local_path"])
        if path.stat().st_size > 5_000_000:
            return None
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != asset["sha256"]:
            return None
        return "data:" + asset["mime_type"] + ";base64," + base64.b64encode(content).decode("ascii")
    except (OSError, KeyError):
        return None


def bundled_equipment_display() -> dict[str, dict]:
    return _bundled_display("equipment.json")


def bundled_item_display() -> dict[str, dict]:
    return _bundled_display("items.json")


def _bundled_display(filename: str) -> dict[str, dict]:
    manifest = json.loads((OFFICIAL_ASSETS / filename).read_text())
    result = {}
    for code, metadata in manifest["items"].items():
        row = dict(metadata)
        asset = row.get("asset")
        if asset:
            row["image_src"] = asset_data_uri({**asset, "local_path": str(OFFICIAL_ASSETS / asset["local_path"])})
        row["observed_at"] = manifest["verified_at"]
        result[code] = row
    return result
