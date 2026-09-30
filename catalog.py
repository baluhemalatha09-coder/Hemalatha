"""Mock product/service catalogue (simulated Amazon, Flipkart, IKEA, Swiggy, Zomato, OYO data).

Used for (1) grounding Gemini prompts with realistic INR price ranges and
(2) building fallback recommendations when the AI is unavailable.
Prices are illustrative estimates, not live data.
"""
from urllib.parse import quote_plus

SEARCH_URLS = {
    "amazon": "https://www.amazon.in/s?k={q}",
    "flipkart": "https://www.flipkart.com/search?q={q}",
    "ikea": "https://www.ikea.com/in/en/search/?q={q}",
    "swiggy": "https://www.swiggy.com/search?query={q}",
    "zomato": "https://www.zomato.com/search?q={q}",
    "oyo": "https://www.oyorooms.com/search/?q={q}",
}


def search_url(platform: str, name: str) -> str:
    key = (platform or "").strip().lower()
    template = SEARCH_URLS.get(key, SEARCH_URLS["amazon"])
    return template.format(q=quote_plus(name or ""))


def _i(name, platform, price):
    return {"name": name, "platform": platform, "price": price}


CATALOG = {
    # ---- Home ----
    "lights": [
        _i("LED Ceiling Panel Light 15W", "Amazon", 650),
        _i("Pendant Lamp with Fabric Shade", "IKEA", 1990),
        _i("Designer Chandelier Light", "Flipkart", 4500),
    ],
    "ceiling_fans": [
        _i("Energy-Saving BLDC Ceiling Fan 1200mm", "Amazon", 3200),
        _i("Decorative Ceiling Fan with Remote", "Flipkart", 4800),
        _i("Premium Designer Ceiling Fan", "Amazon", 7500),
    ],
    "dining_tables": [
        _i("4-Seater Wooden Dining Set", "Flipkart", 12500),
        _i("Compact Extendable Dining Table", "IKEA", 15990),
        _i("6-Seater Sheesham Dining Set", "Amazon", 24000),
    ],
    "sofas": [
        _i("3-Seater Fabric Sofa", "Flipkart", 14500),
        _i("Compact 2-Seater Sofa", "IKEA", 19990),
        _i("L-Shaped Sectional Sofa", "Amazon", 32000),
    ],
    "beds": [
        _i("Queen Size Engineered Wood Bed", "Flipkart", 13500),
        _i("Storage Bed with Mattress Support", "IKEA", 18990),
        _i("King Size Solid Wood Bed", "Amazon", 29500),
    ],
    "decor": [
        _i("Framed Wall Art Set of 3", "Amazon", 899),
        _i("Indoor Plant with Ceramic Pot", "IKEA", 499),
        _i("Textured Cushion Covers (Set of 5)", "Flipkart", 649),
    ],
    # ---- Party ----
    "catering": [
        _i("Party Combo Meals (per plate)", "Swiggy", 350),
        _i("Buffet Catering Tray Package (per plate)", "Zomato", 550),
        _i("Premium Multi-Cuisine Catering (per plate)", "Zomato", 900),
    ],
    "decoration": [
        _i("Birthday Balloon & Banner Decoration Kit", "Amazon", 1200),
        _i("Fairy Light & Theme Backdrop Package", "Flipkart", 3500),
        _i("Premium Floral Stage Decoration", "Zomato", 12000),
    ],
    "entertainment": [
        _i("Bluetooth Party Speaker with Mic", "Amazon", 3500),
        _i("DJ & Sound Setup (3 hours)", "Zomato", 8000),
        _i("Live Band / Anchor Booking", "Zomato", 20000),
    ],
    "venue": [
        _i("OYO Townhouse Banquet Hall (per event)", "OYO", 15000),
        _i("OYO Rooftop Party Space (per event)", "OYO", 9000),
        _i("Home Party - Furniture & Setup Rental", "Amazon", 3000),
    ],
    # ---- Jewelry ----
    "necklaces": [
        _i("Kundan Choker Necklace Set", "Amazon", 1499),
        _i("Gold-Plated Layered Necklace", "Flipkart", 999),
        _i("Sterling Silver Pendant Necklace", "Amazon", 2499),
    ],
    "earrings": [
        _i("Oxidised Silver Jhumka Earrings", "Flipkart", 499),
        _i("American Diamond Studs", "Amazon", 799),
        _i("Pearl Drop Earrings", "Amazon", 1299),
    ],
    "bracelets": [
        _i("Gold-Plated Kada Bracelet", "Flipkart", 1199),
        _i("Charm Bracelet Sterling Silver", "Amazon", 1899),
    ],
    "rings": [
        _i("Adjustable Cocktail Ring", "Flipkart", 399),
        _i("Solitaire-Style Cubic Zirconia Ring", "Amazon", 1499),
    ],
}

PLATFORM_HINTS = {
    "home": "Amazon, Flipkart, IKEA",
    "party": "Swiggy, Zomato, OYO, Amazon, Flipkart",
    "jewelry": "Amazon, Flipkart",
}


def catalog_snippet(categories) -> str:
    """Compact text version of the catalogue to ground the prompt."""
    lines = []
    for cat in categories:
        for it in CATALOG.get(cat, []):
            lines.append(f"- [{cat}] {it['name']} | {it['platform']} | approx INR {it['price']}")
    return "\n".join(lines)
