"""AI + recommendation logic for PocketSmart AI.

* Builds prompts for the Home, Party and Jewelry planners.
* Calls Gemini (text, or text + image for jewelry) via the google-genai SDK.
* Validates / normalises the AI JSON and enforces the budget.
* Falls back to catalogue-based recommendations when the AI is unavailable
  or returns something unusable (Activity 5.4 of the project doc).

This module has no FastAPI dependency so it can be tested on its own.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Optional

from catalog import CATALOG, PLATFORM_HINTS, catalog_snippet, search_url

logger = logging.getLogger("pocketsmart.ai")

SYSTEM_INSTRUCTION = (
    "You are PocketSmart AI, an expert budget-aware shopping and planning assistant for "
    "users in India. All prices are in Indian Rupees (INR). Recommend realistic products or "
    "services from popular platforms (Amazon, Flipkart, IKEA, Swiggy, Zomato, OYO). "
    "Never exceed the user's total budget. Respond with valid JSON only - no markdown, "
    "no commentary."
)

JSON_SHAPE = """Return JSON with exactly this shape:
{
  "summary": "2-3 sentence overview of the plan",
  "sections": [
    {
      "title": "Section name (e.g. Lighting)",
      "allocated_budget": 0,
      "items": [
        {
          "name": "Specific product/service name",
          "platform": "Amazon | Flipkart | IKEA | Swiggy | Zomato | OYO",
          "price": 0,
          "quantity": 1,
          "reason": "One short sentence on why it fits"
        }
      ]
    }
  ],
  "tips": ["2-4 short money-saving or planning tips"]
}
Rules: "price" is the per-unit price in INR (number). The sum of price x quantity across
ALL items must be less than or equal to the total budget. Sum of allocated_budget must not
exceed the total budget."""


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _num(value: Any, default: float = 0.0) -> float:
    try:
        if isinstance(value, str):
            value = re.sub(r"[^\d.]", "", value) or default
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _extract_json(text: str) -> dict:
    """Parse JSON from a model reply, tolerating ```json fences and stray text."""
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise


def gemini_configured() -> bool:
    key = os.getenv("GEMINI_API_KEY", "").strip()
    return bool(key) and not key.startswith("your_")


def _model_chain() -> list[str]:
    primary = os.getenv("GEMINI_MODEL", "gemini-3-flash-preview").strip()
    fallbacks = [m.strip() for m in os.getenv("GEMINI_FALLBACK_MODELS", "").split(",") if m.strip()]
    chain = []
    for m in [primary, *fallbacks]:
        if m and m not in chain:
            chain.append(m)
    return chain


# --------------------------------------------------------------------------- #
# Gemini call
# --------------------------------------------------------------------------- #
def call_gemini(prompt: str, image_bytes: Optional[bytes] = None, mime_type: str = "image/jpeg") -> dict:
    """Send a prompt (optionally with an image) to Gemini and return parsed JSON.

    Tries GEMINI_MODEL, then each model in GEMINI_FALLBACK_MODELS.
    Raises RuntimeError if every model fails.
    """
    if not gemini_configured():
        raise RuntimeError("GEMINI_API_KEY is not configured")

    from google import genai  # imported lazily so the app still starts without the SDK
    from google.genai import types

    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY").strip())
    contents: list[Any] = [prompt]
    if image_bytes:
        contents.append(types.Part.from_bytes(data=image_bytes, mime_type=mime_type))

    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION,
        response_mime_type="application/json",
        temperature=0.4,
    )

    last_error: Optional[Exception] = None
    for model in _model_chain():
        try:
            response = client.models.generate_content(model=model, contents=contents, config=config)
            data = _extract_json(response.text)
            if isinstance(data, dict):
                data["_model"] = model
                return data
        except Exception as exc:  # noqa: BLE001 - try next model
            logger.warning("Gemini model %s failed: %s", model, exc)
            last_error = exc
    raise RuntimeError(f"All Gemini models failed: {last_error}")


# --------------------------------------------------------------------------- #
# Normalisation + budget enforcement
# --------------------------------------------------------------------------- #
def normalize_result(raw: dict, budget: float) -> dict:
    """Clean AI output into a predictable structure and compute totals."""
    sections = []
    for sec in raw.get("sections") or []:
        items = []
        for it in sec.get("items") or []:
            name = str(it.get("name", "")).strip()
            if not name:
                continue
            platform = str(it.get("platform", "Amazon")).strip() or "Amazon"
            price = max(_num(it.get("price")), 0)
            qty = max(int(_num(it.get("quantity"), 1)), 1)
            items.append(
                {
                    "name": name,
                    "platform": platform,
                    "price": round(price),
                    "quantity": qty,
                    "subtotal": round(price * qty),
                    "reason": str(it.get("reason", "")).strip(),
                    "url": search_url(platform, name),
                }
            )
        if items:
            sections.append(
                {
                    "title": str(sec.get("title", "Recommendations")).strip() or "Recommendations",
                    "allocated_budget": round(_num(sec.get("allocated_budget"))),
                    "items": items,
                    "subtotal": sum(i["subtotal"] for i in items),
                }
            )
    total = sum(s["subtotal"] for s in sections)
    tips = [str(t).strip() for t in (raw.get("tips") or []) if str(t).strip()]
    return {
        "summary": str(raw.get("summary", "")).strip(),
        "sections": sections,
        "tips": tips,
        "budget": round(budget),
        "total": total,
        "remaining": round(budget) - total,
        "within_budget": total <= round(budget),
        "source": "ai",
        "model": raw.get("_model", ""),
    }


def _is_usable(result: dict) -> bool:
    return bool(result["sections"]) and result["within_budget"]


def _run_ai(prompt: str, budget: float, image: Optional[tuple[bytes, str]] = None) -> Optional[dict]:
    """Call Gemini, retry once with a corrective note if over budget. None on failure."""
    img_bytes, mime = image if image else (None, "image/jpeg")
    try:
        result = normalize_result(call_gemini(prompt, img_bytes, mime), budget)
        if _is_usable(result):
            return result
        if result["sections"] and not result["within_budget"]:
            retry = (
                prompt
                + f"\n\nYour previous answer totalled INR {result['total']}, which exceeds the budget of "
                f"INR {round(budget)}. Produce a cheaper plan that stays within the budget."
            )
            result = normalize_result(call_gemini(retry, img_bytes, mime), budget)
            if _is_usable(result):
                return result
    except Exception as exc:  # noqa: BLE001
        logger.warning("AI generation failed, using fallback: %s", exc)
    return None


# --------------------------------------------------------------------------- #
# Fallback (catalogue-based) recommendations
# --------------------------------------------------------------------------- #
def _pick(category: str, qty: int, allocation: float) -> Optional[dict]:
    """Best catalogue item for the category whose qty x price fits the allocation."""
    options = sorted(CATALOG.get(category, []), key=lambda i: i["price"])
    if not options:
        return None
    fitting = [o for o in options if o["price"] * qty <= allocation]
    chosen = fitting[-1] if fitting else options[0]
    return {**chosen, "quantity": qty, "reason": "Best value match from our catalogue for this budget."}


def _fallback_plan(budget: float, wants: list[tuple[str, str, int, float]], summary: str, tips: list[str]) -> dict:
    """wants = [(section title, catalogue category, quantity, weight)]"""
    total_w = sum(w for *_, w in wants) or 1
    sections = []
    for title, cat, qty, weight in wants:
        allocation = budget * weight / total_w
        item = _pick(cat, max(qty, 1), allocation)
        if item:
            sections.append({"title": title, "allocated_budget": round(allocation), "items": [item]})

    raw = {"summary": summary, "sections": sections, "tips": tips}
    result = normalize_result(raw, budget)

    # Trim by removing the most expensive section until we fit (keeps at least one).
    while not result["within_budget"] and len(sections) > 1:
        sections.remove(max(sections, key=lambda s: s["items"][0]["price"] * s["items"][0]["quantity"]))
        result = normalize_result({"summary": summary, "sections": sections, "tips": tips}, budget)

    result["source"] = "fallback"
    if not result["within_budget"]:
        result["summary"] += " Note: your budget is very low for these items; consider increasing it or reducing quantities."
    return result


def _to_int(v: Any, default: int = 0) -> int:
    return int(_num(v, default))


# --------------------------------------------------------------------------- #
# HOME planner
# --------------------------------------------------------------------------- #
HOME_ITEMS = [
    ("lights", "Lighting", "lights", 2.0),
    ("ceiling_fans", "Ceiling Fans", "ceiling_fans", 2.5),
    ("dining_tables", "Dining Tables", "dining_tables", 4.0),
    ("sofas", "Sofas", "sofas", 4.0),
    ("beds", "Beds", "beds", 4.0),
]


def generate_home_recommendations(data: dict) -> dict:
    budget = _num(data["budget"])
    rooms = data.get("rooms") or []
    style = data.get("style") or "Modern"
    notes = data.get("notes") or "None"
    quantities = {key: _to_int(data.get(key)) for key, *_ in HOME_ITEMS}
    wanted = {k: q for k, q in quantities.items() if q > 0}
    cats = list(wanted) + ["decor"]

    quantity_text = ", ".join(f"{k.replace('_', ' ')}: {q}" for k, q in wanted.items()) or "none specified"
    prompt = f"""Plan a home interior purchase.
Total budget: INR {round(budget)}
Rooms: {', '.join(rooms) or 'Not specified'}
Style preference: {style}
Required quantities: {quantity_text}
Additional requirements: {notes}
Preferred platforms: {PLATFORM_HINTS['home']}

Reference catalogue with approximate prices (use as a guide, you may suggest similar items):
{catalog_snippet(cats)}

Create one section per required item type (plus a small "Decor & Accents" section if budget allows).
Balance functionality, style and price.
{JSON_SHAPE}"""

    ai = _run_ai(prompt, budget)
    if ai:
        return ai

    wants = [(title, key, wanted[key], w) for key, title, _f, w in HOME_ITEMS if key in wanted]
    if not wants:
        wants = [("Lighting", "lights", 4, 2.0), ("Ceiling Fans", "ceiling_fans", 2, 2.5)]
    wants.append(("Decor & Accents", "decor", 2, 1.0))
    return _fallback_plan(
        budget,
        wants,
        f"A {style.lower()} starter plan for {', '.join(rooms) or 'your home'} built from our catalogue.",
        [
            "Compare the same product on multiple platforms before buying.",
            "Buy big-ticket items during platform sale events for 10-30% savings.",
            "Choose energy-efficient fans and LED lights to cut electricity bills.",
        ],
    )


# --------------------------------------------------------------------------- #
# PARTY planner
# --------------------------------------------------------------------------- #
PARTY_WEIGHTS = {  # catering, decoration, entertainment, venue
    "birthday": (0.45, 0.20, 0.15, 0.20),
    "corporate": (0.40, 0.10, 0.15, 0.35),
    "wedding": (0.40, 0.25, 0.15, 0.20),
    "anniversary": (0.40, 0.25, 0.10, 0.25),
    "other": (0.45, 0.20, 0.15, 0.20),
}


def generate_party_recommendations(data: dict) -> dict:
    budget = _num(data["budget"])
    guests = max(_to_int(data.get("guests"), 1), 1)
    event = (data.get("event_type") or "Birthday").strip()
    venue_type = data.get("venue_type") or "Not specified"
    city = data.get("city") or "Not specified"
    notes = data.get("notes") or "None"
    w = PARTY_WEIGHTS.get(event.lower(), PARTY_WEIGHTS["other"])

    prompt = f"""Plan a party within budget.
Event type: {event}
Total budget: INR {round(budget)}
Guest count: {guests}
Venue type: {venue_type}
City: {city}
Additional requirements: {notes}
Suggested split: Catering {int(w[0]*100)}%, Decoration {int(w[1]*100)}%, Entertainment {int(w[2]*100)}%, Venue/Stay {int(w[3]*100)}% (adjust sensibly for the event type).
Sources: catering from Swiggy/Zomato, venue or guest accommodation from OYO, decor/entertainment from Amazon/Flipkart/Zomato.

Reference catalogue with approximate prices:
{catalog_snippet(['catering', 'decoration', 'entertainment', 'venue'])}

Catering price must be per plate x guest count (set quantity = guest count).
Use exactly these sections: Catering, Decoration, Entertainment, Venue.
{JSON_SHAPE}"""

    ai = _run_ai(prompt, budget)
    if ai:
        return ai

    wants = [
        ("Catering", "catering", guests, w[0]),
        ("Decoration", "decoration", 1, w[1]),
        ("Entertainment", "entertainment", 1, w[2]),
    ]
    if venue_type.lower() not in ("home", "at home"):
        wants.append(("Venue", "venue", 1, w[3]))
    return _fallback_plan(
        budget,
        wants,
        f"A {event.lower()} plan for {guests} guests, split across catering, decor, entertainment and venue.",
        [
            "Order catering at least 3 days ahead to lock in group discounts.",
            "Confirm final guest count 48 hours before the event to avoid over-ordering.",
            "Keep 5-10% of the budget aside for surprises.",
        ],
    )


# --------------------------------------------------------------------------- #
# JEWELRY planner (text + optional image)
# --------------------------------------------------------------------------- #
def generate_jewelry_recommendations(data: dict, image: Optional[tuple[bytes, str]] = None) -> dict:
    budget = _num(data["budget"])
    occasion = data.get("occasion") or "Wedding"
    style = data.get("style") or "Traditional"
    metal = data.get("metal") or "Any"
    notes = data.get("notes") or "None"

    image_note = (
        "An outfit photo is attached. Consider its colours, neckline and overall aesthetic so the jewelry matches."
        if image
        else "No outfit photo was provided."
    )
    prompt = f"""Recommend jewelry.
Total budget: INR {round(budget)}
Occasion: {occasion}
Style preference: {style}
Metal / finish preference: {metal}
Additional requirements: {notes}
{image_note}
Platforms: {PLATFORM_HINTS['jewelry']}

Reference catalogue with approximate prices:
{catalog_snippet(['necklaces', 'earrings', 'bracelets', 'rings'])}

Create sections such as Necklaces, Earrings, Bracelets and Rings. In the summary, mention how the
picks match the occasion{' and the outfit colours' if image else ''}.
{JSON_SHAPE}"""

    ai = _run_ai(prompt, budget, image)
    if ai:
        return ai

    wants = [("Necklaces", "necklaces", 1, 3.0), ("Earrings", "earrings", 1, 2.0), ("Bracelets", "bracelets", 1, 1.5), ("Rings", "rings", 1, 1.5)]
    return _fallback_plan(
        budget,
        wants,
        f"A {style.lower()} jewelry set for a {occasion.lower()} within your budget.",
        [
            "Pick one statement piece (necklace or earrings) and keep the rest subtle.",
            "Match metal tone with your outfit's embroidery or accessories.",
            "Check return policies on Amazon and Flipkart before buying jewelry.",
        ],
    )
