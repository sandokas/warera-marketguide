"""Refresh the portable official item icon bundle from normalized API metadata."""
import hashlib
import json
from datetime import datetime, timezone
from warera_quant.api_client import WarEraApiClient
from warera_quant.warera_api import WarEraMarketApi
from warera_quant.display_assets import OFFICIAL_ASSETS, normalize_image


def main():
    client = WarEraApiClient()
    client.session.headers["User-Agent"] = "Mozilla/5.0"
    items = WarEraMarketApi(client).get_item_display()
    assets = {}
    for item in items:
        url = item["image_url"]
        if url not in assets:
            content, mime = client.get_public_bytes(url)
            content, mime, width, height = normalize_image(content, mime)
            filename = url.split("/")[-1].split("?")[0]
            (OFFICIAL_ASSETS / filename).write_bytes(content)
            assets[url] = dict(local_path=filename, mime_type=mime,
                sha256=hashlib.sha256(content).hexdigest(), width=width, height=height,
                byte_count=len(content), source_url=url)
        item["asset"] = assets[url]
    manifest = {"verified_at": datetime.now(timezone.utc).isoformat(),
                "items": {item["item_code"]: item for item in items}}
    (OFFICIAL_ASSETS / "items.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Bundled {len(items)} item mappings and {len(assets)} images.")


if __name__ == "__main__":
    main()
