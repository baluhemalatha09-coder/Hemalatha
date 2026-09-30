"""PocketSmart AI - FastAPI application entry point.

Run with:  python main.py     (or)     uvicorn main:app --reload
"""
import io
import logging
import os
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse, RedirectResponse  # noqa: E402
from fastapi.security import OAuth2PasswordRequestForm  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from fastapi.templating import Jinja2Templates  # noqa: E402
from PIL import Image, UnidentifiedImageError  # noqa: E402
from starlette.middleware.sessions import SessionMiddleware  # noqa: E402

import auth  # noqa: E402
import database  # noqa: E402
import gemini_utils  # noqa: E402
from auth import NotAuthenticated, get_current_user  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pocketsmart")

UPLOAD_DIR = BASE_DIR / "static" / "uploads"
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_BUDGET = 100_000_000
IMAGE_FORMATS = {"JPEG": ("image/jpeg", ".jpg"), "PNG": ("image/png", ".png"), "WEBP": ("image/webp", ".webp")}

CATEGORY_LABELS = {"home": "Home Interior", "party": "Party", "jewelry": "Jewelry"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: create folders + tables, report Gemini status."""
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    database.init_db()
    if gemini_utils.gemini_configured():
        logger.info("Gemini configured. Model chain: %s", gemini_utils._model_chain())
    else:
        logger.warning("GEMINI_API_KEY not set - planners will use catalogue fallback recommendations.")
    yield


app = FastAPI(title="PocketSmart AI", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SessionMiddleware, secret_key=auth.secret_key(), same_site="lax", max_age=60 * 60 * 24)

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def money(value) -> str:
    try:
        return f"₹{int(round(float(value))):,}"
    except (TypeError, ValueError):
        return "₹0"


templates.env.filters["money"] = money


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def wants_json(request: Request) -> bool:
    return "application/json" in request.headers.get("accept", "")


def render(request: Request, name: str, user: Optional[dict] = None, status_code: int = 200, **ctx):
    if user is None:
        user = auth.get_optional_user(request)
    context = {"user": user, **ctx}
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def parse_budget(raw: str, minimum: float = 100) -> float:
    try:
        value = float(str(raw).replace(",", "").replace("₹", "").strip())
    except ValueError:
        raise ValueError("Please enter a valid budget amount.")
    if value < minimum:
        raise ValueError(f"Budget must be at least ₹{int(minimum)}.")
    if value > MAX_BUDGET:
        raise ValueError("Budget is too large. Please enter a realistic amount.")
    return value


def parse_int(raw: str, label: str, minimum: int = 0, maximum: int = 1000) -> int:
    raw = (raw or "").strip()
    if raw == "":
        return minimum
    try:
        value = int(float(raw))
    except ValueError:
        raise ValueError(f"{label} must be a whole number.")
    if not minimum <= value <= maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}.")
    return value


def clean_text(value: Optional[str], limit: int = 500) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip()[:limit]


def read_and_validate_image(upload: UploadFile):
    """Return (bytes, mime, saved_relative_path). Raises ValueError for bad files."""
    data = upload.file.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Image is too large (max 5 MB).")
    if not data:
        raise ValueError("The uploaded image is empty.")
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.verify()
            fmt = img.format
    except (UnidentifiedImageError, OSError):
        raise ValueError("Please upload a valid image (JPG, PNG or WEBP).")
    if fmt not in IMAGE_FORMATS:
        raise ValueError("Unsupported image type. Use JPG, PNG or WEBP.")
    mime, ext = IMAGE_FORMATS[fmt]
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}{ext}"
    (UPLOAD_DIR / filename).write_bytes(data)
    return data, mime, f"uploads/{filename}"


def save_and_redirect(request: Request, user: dict, category: str, budget: float, inputs: dict, result: dict):
    history_id = database.add_history(user["id"], category, budget, inputs, result)
    request.session["last_category"] = category
    request.session["last_history_id"] = history_id
    return RedirectResponse(f"/recommendations/{history_id}", status_code=303)


# --------------------------------------------------------------------------- #
# Error handling
# --------------------------------------------------------------------------- #
@app.exception_handler(NotAuthenticated)
async def not_authenticated_handler(request: Request, exc: NotAuthenticated):
    if wants_json(request) or request.headers.get("authorization"):
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)
    return RedirectResponse("/login", status_code=303)


# --------------------------------------------------------------------------- #
# Public pages + auth
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    return {"status": "ok", "gemini_configured": gemini_utils.gemini_configured()}


@app.get("/")
def index(request: Request):
    return render(request, "index.html")


@app.get("/register")
def register_page(request: Request):
    if auth.get_optional_user(request):
        return RedirectResponse("/dashboard", status_code=303)
    return render(request, "register.html")


@app.post("/register")
def register(
    request: Request,
    username: str = Form(...),
    email: str = Form(""),
    password: str = Form(...),
    confirm_password: str = Form(...),
):
    username, email = username.strip(), email.strip()
    error = None
    if not re.fullmatch(r"[A-Za-z0-9_]{3,30}", username):
        error = "Username must be 3-30 characters: letters, numbers or underscore."
    elif email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        error = "Please enter a valid email address."
    elif len(password) < 6:
        error = "Password must be at least 6 characters."
    elif password != confirm_password:
        error = "Passwords do not match."
    elif database.get_user_by_username(username):
        error = "That username is already taken."

    if error:
        return render(request, "register.html", status_code=400, error=error, form={"username": username, "email": email})

    try:
        database.create_user(username, email, auth.hash_password(password))
    except Exception:  # sqlite3.IntegrityError from a race condition
        return render(request, "register.html", status_code=400, error="That username is already taken.")
    return render(request, "login.html", success="Account created! Please sign in.", form={"username": username})


@app.get("/login")
def login_page(request: Request):
    if auth.get_optional_user(request):
        return RedirectResponse("/dashboard", status_code=303)
    return render(request, "login.html")


@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    user = auth.authenticate_user(username, password)
    if not user:
        return render(request, "login.html", status_code=401, error="Invalid username or password.", form={"username": username})
    token = auth.create_access_token(user["id"])
    response = RedirectResponse("/dashboard", status_code=303)
    response.set_cookie(
        "access_token", token, httponly=True, samesite="lax", max_age=auth.token_lifetime_minutes() * 60
    )
    request.session.clear()
    request.session.update({"user_id": user["id"], "username": user["username"], "logged_in": True})
    return response


@app.api_route("/logout", methods=["GET", "POST"])
def logout(request: Request):
    request.session.clear()
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("access_token")
    return response


@app.post("/token")
def token(form: OAuth2PasswordRequestForm = Depends()):
    """OAuth2 password flow - returns a JWT for API clients (Swagger UI, curl, etc.)."""
    user = auth.authenticate_user(form.username, form.password)
    if not user:
        return JSONResponse({"detail": "Incorrect username or password"}, status_code=401)
    return {"access_token": auth.create_access_token(user["id"]), "token_type": "bearer"}


# --------------------------------------------------------------------------- #
# Session endpoints
# --------------------------------------------------------------------------- #
@app.get("/session-info")
def session_info(request: Request, user: dict = Depends(get_current_user)):
    return {
        "user_id": user["id"],
        "username": user["username"],
        "logged_in": True,
    }


@app.get("/session-data")
def session_data(request: Request, user: dict = Depends(get_current_user)):
    stats = database.history_stats(user["id"])
    return {
        "user_id": user["id"],
        "username": user["username"],
        "last_category": request.session.get("last_category"),
        "last_history_id": request.session.get("last_history_id"),
        "stats": stats,
    }


# --------------------------------------------------------------------------- #
# Dashboard + planner pages
# --------------------------------------------------------------------------- #
@app.get("/dashboard")
def dashboard(request: Request, user: dict = Depends(get_current_user)):
    return render(
        request,
        "dashboard.html",
        user=user,
        recent=database.list_history(user["id"], limit=5),
        stats=database.history_stats(user["id"]),
        labels=CATEGORY_LABELS,
    )


@app.get("/home-planner")
def home_planner_page(request: Request, user: dict = Depends(get_current_user)):
    return render(request, "home_planner.html", user=user)


@app.get("/party-planner")
def party_planner_page(request: Request, user: dict = Depends(get_current_user)):
    return render(request, "party_planner.html", user=user)


@app.get("/jewelry-planner")
def jewelry_planner_page(request: Request, user: dict = Depends(get_current_user)):
    return render(request, "jewelry_planner.html", user=user)


# --------------------------------------------------------------------------- #
# Recommendation generation (each accepts an HTML form, saves history, redirects)
# --------------------------------------------------------------------------- #
@app.post("/generate-home")
def generate_home(
    request: Request,
    budget: str = Form(...),
    rooms: List[str] = Form(default=[]),
    lights: str = Form("0"),
    ceiling_fans: str = Form("0"),
    dining_tables: str = Form("0"),
    sofas: str = Form("0"),
    beds: str = Form("0"),
    style: str = Form("Modern"),
    notes: str = Form(""),
    user: dict = Depends(get_current_user),
):
    try:
        budget_val = parse_budget(budget, minimum=1000)
        data = {
            "budget": budget_val,
            "rooms": [clean_text(r, 40) for r in rooms][:10],
            "lights": parse_int(lights, "Lights"),
            "ceiling_fans": parse_int(ceiling_fans, "Ceiling fans"),
            "dining_tables": parse_int(dining_tables, "Dining tables"),
            "sofas": parse_int(sofas, "Sofas"),
            "beds": parse_int(beds, "Beds"),
            "style": clean_text(style, 40) or "Modern",
            "notes": clean_text(notes),
        }
        if not data["rooms"]:
            raise ValueError("Please select at least one room.")
        if not any(data[k] for k in ("lights", "ceiling_fans", "dining_tables", "sofas", "beds")):
            raise ValueError("Please enter a quantity for at least one item.")
    except ValueError as exc:
        return render(request, "home_planner.html", user=user, status_code=400, error=str(exc))

    result = gemini_utils.generate_home_recommendations(data)
    return save_and_redirect(request, user, "home", budget_val, data, result)


@app.post("/generate-party")
def generate_party(
    request: Request,
    budget: str = Form(...),
    guests: str = Form(...),
    event_type: str = Form("Birthday"),
    venue_type: str = Form("Banquet Hall"),
    city: str = Form(""),
    notes: str = Form(""),
    user: dict = Depends(get_current_user),
):
    try:
        budget_val = parse_budget(budget, minimum=2000)
        data = {
            "budget": budget_val,
            "guests": parse_int(guests, "Guest count", minimum=1, maximum=5000),
            "event_type": clean_text(event_type, 40) or "Birthday",
            "venue_type": clean_text(venue_type, 60),
            "city": clean_text(city, 60),
            "notes": clean_text(notes),
        }
    except ValueError as exc:
        return render(request, "party_planner.html", user=user, status_code=400, error=str(exc))

    result = gemini_utils.generate_party_recommendations(data)
    return save_and_redirect(request, user, "party", budget_val, data, result)


@app.post("/generate-jewelry")
def generate_jewelry(
    request: Request,
    budget: str = Form(...),
    occasion: str = Form("Wedding"),
    style: str = Form("Traditional"),
    metal: str = Form("Any"),
    notes: str = Form(""),
    outfit_image: Optional[UploadFile] = File(None),
    user: dict = Depends(get_current_user),
):
    image = None
    image_path = None
    try:
        budget_val = parse_budget(budget, minimum=500)
        if outfit_image is not None and outfit_image.filename:
            img_bytes, mime, image_path = read_and_validate_image(outfit_image)
            image = (img_bytes, mime)
        data = {
            "budget": budget_val,
            "occasion": clean_text(occasion, 40) or "Wedding",
            "style": clean_text(style, 40) or "Traditional",
            "metal": clean_text(metal, 40) or "Any",
            "notes": clean_text(notes),
        }
    except ValueError as exc:
        return render(request, "jewelry_planner.html", user=user, status_code=400, error=str(exc))

    result = gemini_utils.generate_jewelry_recommendations(data, image)
    if image_path:
        data["image_path"] = image_path
    return save_and_redirect(request, user, "jewelry", budget_val, data, result)


# --------------------------------------------------------------------------- #
# Results + history
# --------------------------------------------------------------------------- #
@app.get("/recommendations/{item_id}")
def recommendation_page(item_id: int, request: Request, user: dict = Depends(get_current_user)):
    item = database.get_history_item(user["id"], item_id)
    if not item:
        return render(request, "history.html", user=user, status_code=404, items=[], labels=CATEGORY_LABELS,
                      error="That recommendation was not found.")
    return render(request, "result.html", user=user, item=item, labels=CATEGORY_LABELS)


@app.get("/recommendations-details")
def recommendation_details(id: int, user: dict = Depends(get_current_user)):
    """JSON details of one saved recommendation."""
    item = database.get_history_item(user["id"], id)
    if not item:
        return JSONResponse({"detail": "Recommendation not found"}, status_code=404)
    return item


@app.get("/history")
def history(request: Request, user: dict = Depends(get_current_user)):
    items = database.list_history(user["id"], limit=100)
    if wants_json(request):
        return items
    return render(request, "history.html", user=user, items=items, labels=CATEGORY_LABELS)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8000")), reload=True)
