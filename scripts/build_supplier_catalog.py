"""Build the checked-in standard Banana product map from its public Woo catalog."""
import json
import re
import ssl
import sys
from pathlib import Path
from urllib.parse import unquote
from urllib.request import urlopen

import certifi


ROOT = Path(__file__).resolve().parents[1]
PRICE_FILE = ROOT / "country_prices.json"
OUTPUT_FILE = ROOT / "supplier_catalog.json"
PRODUCTS_URL = "https://esimbanana.com/wp-json/wc/store/v1/products?per_page=100&page={}"


def normalized(value):
    return re.sub(r"[^a-z0-9]+", " ", unquote(str(value)).lower()).strip()


def parsed_plan(value):
    value = unquote(str(value)).upper().replace("-", " ").replace("_", " ")
    match = re.search(r"(?<!\d)(1|3|5|10|20|50|100)\s*GB.*?(7|30|90|180)\s*(?:DAYS?|D\b)", value)
    if not match:
        return None
    gigabytes, days = map(int, match.groups())
    return f"{gigabytes}GB / {days} дней", gigabytes * 1024, days


def fetch_products():
    context = ssl.create_default_context(cafile=certifi.where())
    products = []
    page = 1
    while True:
        with urlopen(PRODUCTS_URL.format(page), timeout=30, context=context) as response:
            products.extend(json.load(response))
            total_pages = int(response.headers.get("X-WP-TotalPages", page))
        if page >= total_pages:
            return products
        page += 1


def build_catalog(prices, products):
    candidates = []
    for product in products:
        if str(product.get("sku") or "").startswith("UNL-") or "безлимит" in str(product.get("name") or "").lower():
            continue
        variations = {}
        for variation in product.get("variations") or []:
            attributes = variation.get("attributes") or []
            parsed = parsed_plan(attributes[0].get("value", "") if attributes else "")
            if parsed:
                tariff, refill_mb, refill_days = parsed
                variations[tariff] = (int(variation["id"]), refill_mb, refill_days)
        if variations:
            name = str(product.get("name") or "")
            candidates.append({
                "id": int(product["id"]),
                "segments": {normalized(part) for part in re.split(r"[/|]", name)},
                "variations": variations,
            })

    result = [{
        "country": "Технический тест", "tariff": "Тех тариф",
        "product_id": 40, "variation_id": 0, "refill_mb": 1024, "refill_days": 30,
    }]
    matched_countries = set()
    for country, country_prices in prices.items():
        matches = [item for item in candidates if normalized(country) in item["segments"]]
        if len(matches) != 1:
            continue
        product = matches[0]
        for tariff in country_prices:
            variation = product["variations"].get(tariff)
            if not variation:
                continue
            variation_id, refill_mb, refill_days = variation
            result.append({
                "country": country, "tariff": tariff, "product_id": product["id"],
                "variation_id": variation_id, "refill_mb": refill_mb,
                "refill_days": refill_days,
            })
            matched_countries.add(country)
    result[1:] = sorted(result[1:], key=lambda item: (item["country"].lower(), item["refill_mb"], item["refill_days"]))
    return result, matched_countries


def main():
    prices = json.loads(PRICE_FILE.read_text(encoding="utf-8"))
    catalog, matched = build_catalog(prices, fetch_products())
    if "--write" in sys.argv:
        OUTPUT_FILE.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"supplier products: {len(catalog)}; countries: {len(matched)}; output: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
