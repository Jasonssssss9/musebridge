"""Verify a running gallery against its release, including QR pixels and local JPEGs.

Install playwright, Pillow and zxing-cpp in a QA environment. Install Chromium with
`python -m playwright install chromium` or pass --chrome for an existing browser.
Example: python -m acceptance.gallery_browser http://localhost:8182 knowledge/data/getty-gallery-v2.json
"""
import argparse
import io
import json
from pathlib import Path

import zxingcpp
from PIL import Image
from playwright.sync_api import expect, sync_playwright


def verify_images(api, origin, works, image_dir):
    dimensions = {}
    for art in works:
        url = art["image_url"]
        response = api.get(origin + url)
        if response.status != 200 or response.headers["content-type"].split(";")[0] != "image/jpeg":
            raise AssertionError(f"{url} did not return a JPEG")
        contents = response.body()
        if contents != (image_dir / Path(url).name).read_bytes():
            raise AssertionError(f"{url} differs from the released image")
        with Image.open(io.BytesIO(contents)) as image:
            image.verify()
            dimensions[art["slug"]] = list(image.size)
    return dimensions


def check_layout(page):
    overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    if overflow > 1:
        raise AssertionError(f"Horizontal overflow of {overflow}px on {page.url}")


def check_catalog(page, origin, works):
    assert page.goto(origin + "/").status == 200
    cards = page.locator(".workcard")
    expect(cards).to_have_count(len(works))
    assert cards.evaluate_all("els => els.map(el => el.pathname)") == ["/a/" + w["slug"] for w in works]
    for card, art in zip(cards.all(), works):
        expect(card.locator(".workcard-title")).to_have_text(art["title"])
        image = card.locator("img")
        image.scroll_into_view_if_needed()
        image.evaluate("im => im.decode()")
        expect(image).to_have_attribute("src", art["image_url"])
    check_layout(page)


def read_codes(page, works, origin):
    page.get_by_role("link", name="QR cards").click()
    codes = page.locator(".qrcard svg")
    expect(codes).to_have_count(len(works))
    urls = []
    for code, art in zip(codes.all(), works):
        with Image.open(io.BytesIO(code.screenshot())) as image:
            symbols = zxingcpp.read_barcodes(image)
        expected = origin + "/a/" + art["slug"]
        assert [symbol.text for symbol in symbols] == [expected], f"Wrong QR destination for {art['slug']}"
        urls.append(symbols[0].text)
    check_layout(page)
    return urls


def check_artwork(page, url, art, size):
    slug = art["slug"]
    assert page.goto(url).status == 200, slug
    expect(page.locator("h1")).to_have_text(art["title"])
    expect(page.locator(".artist")).to_have_text(f"{art['artist']}, {art['date_display']}")
    expect(page.get_by_role("link", name="Museum record")).to_have_attribute("href", art["source_url"])
    credit = page.locator("figcaption")
    expect(credit).to_contain_text(art["image_credit"])
    expect(credit).to_contain_text(art["image_license"])
    expect(credit.get_by_role("link")).to_have_attribute("href", art["image_source_url"])
    open_image = page.get_by_role("button", name="Enlarge image of " + art["title"])
    preview = open_image.locator("img")
    expect(preview).to_have_attribute("src", art["image_url"])
    expect(preview).to_have_attribute("alt", art["image_alt"])
    expect(page.locator(".chips button")).to_have_text(art["suggested_questions"])
    expect(page.get_by_role("textbox")).to_be_enabled()
    check_layout(page)
    open_image.click()
    dialog = page.get_by_role("dialog", name=art["title"])
    expect(dialog).to_be_visible()
    for img in (preview, dialog.locator("img")):
        assert img.evaluate("async i => { await i.decode(); return [i.naturalWidth, i.naturalHeight] }") == size, slug
    dialog.get_by_role("button", name="Close").click()
    expect(dialog).to_be_hidden()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("origin", help="Public browser origin configured in Go")
    parser.add_argument("seed", type=Path, help="Exact gallery release JSON")
    parser.add_argument("--chrome", help="Path to an existing Chromium or Chrome binary")
    args = parser.parse_args()
    origin = args.origin.rstrip("/")
    dataset = json.loads(args.seed.read_text(encoding="utf-8"))
    works = dataset["artworks"]
    root = Path(__file__).resolve().parents[1]
    issues = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=args.chrome)
        try:
            for device, width, height in [("desktop", 1440, 900), ("mobile", 390, 844)]:
                mobile = device == "mobile"
                context = browser.new_context(viewport={"width": width, "height": height}, is_mobile=mobile, has_touch=mobile)
                try:
                    page = context.new_page()
                    page.on("pageerror", lambda error: issues.append(str(error)))
                    page.on("console", lambda msg: issues.append(f"{msg.location.get('url', '')} {msg.text}") if msg.type == "error" else None)
                    response = context.request.get(origin + "/api/artworks")
                    assert response.ok and response.json()["artworks"] == works, "Published list differs from release"
                    if not mobile:
                        dimensions = verify_images(context.request, origin, works, root / "web/public/artworks")
                    check_catalog(page, origin, works)
                    urls = read_codes(page, works, origin)
                    for art, url in zip(works, urls):
                        check_artwork(page, url, art, dimensions[art["slug"]])
                    print(f"{device}: {len(works)} gallery cards, decoded QR codes, artwork pages and image dialogs passed.", flush=True)
                finally:
                    context.close()
        finally:
            browser.close()
    assert not issues, issues
    print(f"Release {dataset['dataset_version']}. JPEG dimensions {json.dumps(dimensions)}. No browser errors.")


if __name__ == "__main__":
    main()
