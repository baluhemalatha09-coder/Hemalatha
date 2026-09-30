# PocketSmart AI - Smart Budget & Recommendation Assistant

FastAPI + Jinja2 + Google Gemini. Three planners (Home Interior, Party, Jewelry) that turn a budget
into platform-specific recommendations (Amazon, Flipkart, IKEA, Swiggy, Zomato, OYO), with
registration/login (JWT), session data, and saved history.

## Project structure

```
pocketsmart-ai/
├── main.py              # FastAPI app: routes, auth, sessions, validation, startup
├── gemini_utils.py      # Prompts, Gemini calls (text + image), budget enforcement, fallbacks
├── catalog.py           # Mock platform catalogue + search-link builder
├── auth.py              # bcrypt password hashing + JWT + current-user dependency
├── database.py          # SQLite: users + history
├── requirements.txt
├── .env.example         # copy to .env
├── static/  (styles.css, app.js, uploads/)
├── templates/  (index, login, register, dashboard, home_planner, party_planner,
│                jewelry_planner, result, history, base)
└── tests/  (conftest.py, test_app.py)
```

## 1. Prerequisites
* Python 3.10+  (check: `python --version`)
* VS Code with the **Python** extension
* A free Gemini API key: https://aistudio.google.com/apikey

## 2. Open and set up in VS Code
1. **File → Open Folder…** and choose `pocketsmart-ai`.
2. Open the terminal: **Terminal → New Terminal**.
3. Create and activate a virtual environment:
   * Windows (PowerShell): `python -m venv venv` then `venv\Scripts\Activate.ps1`
     (if blocked: `Set-ExecutionPolicy -Scope Process Bypass`)
   * macOS/Linux: `python3 -m venv venv` then `source venv/bin/activate`
4. VS Code will ask to use the new environment - click **Yes** (or Ctrl+Shift+P → *Python: Select Interpreter* → `venv`).
5. Install dependencies: `pip install -r requirements.txt`

## 3. Configure
1. Copy `.env.example` to `.env` (`copy .env.example .env` on Windows, `cp .env.example .env` elsewhere).
2. Edit `.env`:
   * `GEMINI_API_KEY=` your key
   * `SECRET_KEY=` any long random string
   * `GEMINI_MODEL=` a current Gemini Flash model (see https://ai.google.dev/gemini-api/docs/models).
     The doc's "Gemini 1.5 Flash Pro" is retired; the app tries `GEMINI_MODEL`, then each model in
     `GEMINI_FALLBACK_MODELS`, then built-in catalogue recommendations.

The app still runs with no API key - it just shows catalogue fallback recommendations.

## 4. Run
```
python main.py
```
Open http://127.0.0.1:8000 - register, log in, and try a planner.
API docs (Swagger): http://127.0.0.1:8000/docs
(Alternative: `uvicorn main:app --reload`)

## 5. Test
Automated (uses a temporary DB and never calls the real Gemini API):
```
pytest -v
```
Manual checklist:
1. Register an account, then log in - you land on the Dashboard.
2. **Home planner**: budget 80000, Living Room, 6 lights, 3 fans, 1 dining table → result cards + budget bar.
3. **Party planner**: budget 60000, 40 guests, Birthday → Catering / Decoration / Entertainment / Venue.
4. **Jewelry planner**: budget 8000, Wedding, upload an outfit photo → result page shows your image.
5. Try bad input (budget `10`, guests `0`, a `.txt` renamed to `.png`) → friendly error messages.
6. Open **History** and the API endpoints while logged in: `/session-info`, `/session-data`,
   `/recommendations-details?id=1`.
7. API token: in `/docs` use **POST /token** with your username/password.

## Routes
| Route | Purpose |
|---|---|
| `/`, `/login`, `/register`, `/logout` | Pages + auth |
| `/token` | OAuth2 password flow → JWT |
| `/dashboard`, `/history` | User area |
| `/home-planner`, `/party-planner`, `/jewelry-planner` | Planner forms |
| `POST /generate-home`, `/generate-party`, `/generate-jewelry` | Generate + save + redirect |
| `/recommendations/{id}`, `/recommendations-details?id=` | Result page / JSON |
| `/session-info`, `/session-data` | Session metadata |
| `/health` | Health check |

## Troubleshooting
* **Results say "Catalogue fallback"** - key missing/invalid, model name unavailable, or the AI went over budget twice. Check the terminal log for the Gemini error.
* **`ModuleNotFoundError`** - venv not activated; run `pip install -r requirements.txt` again.
* **Port in use** - set `PORT=8001` in `.env`.
* **Reset data** - stop the app and delete `data/pocketsmart.db`.
