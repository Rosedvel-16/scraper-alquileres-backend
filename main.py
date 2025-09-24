import os
import sys
import re
import threading
import logging
from datetime import datetime, timedelta
from typing import List, Optional, Tuple, Dict, Any

from fastapi import FastAPI, HTTPException, Query, Depends, Request, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession

# ─────────────────────────────────────────────────────────────
# Paths y flag de entorno
# ─────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
IS_VERCEL = os.getenv("VERCEL") == "1"

# ─────────────────────────────────────────────────────────────
# Imports locales
# ─────────────────────────────────────────────────────────────
from db import engine, get_db, Base
from models import User, RoleEnum
from auth import login_handler, get_current_user, require_role, hash_password
from admin_routes import router as admin_router
from routes_public import pub

# ─────────────────────────────────────────────────────────────
# Scraper (con fallback)
# ─────────────────────────────────────────────────────────────
try:
    from scraper import run_scrapers
except ImportError:
    def run_scrapers(**kwargs):
        logging.warning("Scraper no disponible en este entorno.")
        return []

# ─────────────────────────────────────────────────────────────
# App + CORS
# ─────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Scraper de Alquileres API", version="3.1.0")

FRONTEND_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "https://scraper-alquileres-frontend.vercel.app",
    "https://scraper-alquileres-frontend.vercel.app/"
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────────────────────────
# Rutas de Autenticación
# ─────────────────────────────────────────────────────────────
class LoginPayload(BaseModel):
    email: str
    password: str

@app.post("/auth/login")
def login(payload: LoginPayload, db: DBSession = Depends(get_db)):
    return login_handler(db, payload)

# ─────────────────────────────────────────────────────────────
# Paginación / Modelos (refactorizados para ser más concisos)
# ─────────────────────────────────────────────────────────────
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 20

def clamp_page_size(page_size: Optional[int]) -> int:
    return min(page_size or DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE)

def clamp_page(page: Optional[int]) -> int:
    return page if page and page > 0 else 1

def paginate(items: List[dict], page: int, page_size: int) -> Tuple[List[dict], Dict[str, Any]]:
    total = len(items)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = min(page, total_pages)
    start = (page - 1) * page_size
    end = start + page_size
    slice_items = items[start:end]
    meta = {
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages
    }
    return slice_items, meta

# ─────────────────────────────────────────────────────────────
# Búsqueda
# ─────────────────────────────────────────────────────────────
def _search_logic(search_params: Dict[str, Any], page: int, page_size: int) -> Dict[str, Any]:
    page = clamp_page(page)
    page_size = clamp_page_size(page_size)
    record_search(**search_params)
    props = run_scrapers(**search_params)
    
    if not props:
        empty_meta = {"page": 1, "page_size": page_size, "total": 0, "total_pages": 1, "has_prev": False, "has_next": False}
        return {"success": True, "properties": [], "meta": empty_meta, "message": "No se encontraron propiedades."}
    
    props = mark_featured_one(dedupe_by_link(props))
    page_items, meta = paginate(props, page, page_size)
    
    return {"success": True, "properties": page_items, "meta": meta, "message": f"Se encontraron {meta['total']} propiedades."}

@app.get("/search")
async def search_properties_get(
    zona: str = Query(..., description="Zona"),
    dormitorios: str = Query("0", description="Dormitorios (0 = cualquiera)"),
    banos: str = Query("0", description="Baños (0 = cualquiera)"),
    price_min: Optional[int] = Query(None, description="Precio mínimo (S/)"),
    price_max: Optional[int] = Query(None, description="Precio máximo (S/)"),
    palabras_clave: str = Query("", description="Palabras clave"),
    page: int = Query(1, ge=1, description="Página 1-based"),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE, description="Tamaño de página (máx 20)")
):
    search_params = {
        "zona": zona,
        "dormitorios": dormitorios,
        "banos": banos,
        "price_min": price_min,
        "price_max": price_max,
        "palabras_clave": palabras_clave
    }
    return _search_logic(search_params, page, page_size)

@app.post("/search")
async def search_properties_post(
    request: Dict[str, Any],
    page: int = Query(1, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)
):
    return _search_logic(request, page, page_size)

# ─────────────────────────────────────────────────────────────
# Home Feed y Trending
# ─────────────────────────────────────────────────────────────
SEARCH_STATS: Dict[str, int] = {}
STATS_LOCK = threading.Lock()

def _stats_key(zona: str, dormitorios: str, banos: str, price_min: Optional[int], price_max: Optional[int], palabras_clave: str) -> str:
    zona = (zona or "").strip().lower()
    dormitorios = str(dormitorios or "0")
    banos = str(banos or "0")
    pmin = "" if price_min is None else str(price_min)
    pmax = "" if price_max is None else str(price_max)
    palabras = (palabras_clave or "").strip().lower()
    return f"{zona}|{dormitorios}|{banos}|{pmin}|{pmax}|{palabras}"

def record_search(zona: str, dormitorios: str, banos: str, price_min: Optional[int], price_max: Optional[int], palabras_clave: str):
    key = _stats_key(zona, dormitorios, banos, price_min, price_max, palabras_clave)
    with STATS_LOCK:
        SEARCH_STATS[key] = SEARCH_STATS.get(key, 0) + 1

def parse_stats_key(key: str) -> Dict[str, Any]:
    parts = key.split("|")
    return {
        "zona": parts[0],
        "dormitorios": parts[1] if parts[1] else "0",
        "banos": parts[2] if parts[2] else "0",
        "price_min": int(parts[3]) if parts[3] else None,
        "price_max": int(parts[4]) if parts[4] else None,
        "palabras_clave": parts[5] or ""
    }

HOME_CACHE: Dict[str, Any] = {"expires": datetime.min, "payload": None}
HOME_CACHE_TTL = timedelta(minutes=15)

def get_home_cached() -> Optional[dict]:
    if HOME_CACHE["payload"] and datetime.utcnow() < HOME_CACHE["expires"]:
        return HOME_CACHE["payload"]
    return None

def set_home_cached(payload: dict):
    HOME_CACHE["payload"] = payload
    HOME_CACHE["expires"] = datetime.utcnow() + HOME_CACHE_TTL

@app.get("/trending")
async def trending(limit: int = Query(6, ge=1, le=20)):
    with STATS_LOCK:
        items = sorted(SEARCH_STATS.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    payload = []
    for key, count in items:
        parsed = parse_stats_key(key)
        parsed["count"] = count
        payload.append(parsed)
    return {"items": payload, "generated_at": datetime.utcnow().isoformat()}

@app.get("/home-feed")
async def home_feed():
    cached = get_home_cached()
    if cached:
        return cached

    with STATS_LOCK:
        sorted_stats = sorted(SEARCH_STATS.items(), key=lambda kv: kv[1], reverse=True)
    base_queries = [parse_stats_key(key) for key, _ in sorted_stats[:3]]

    if not base_queries:
        base_queries = [
            {"zona": "miraflores", "dormitorios": "0", "banos": "0", "price_min": None, "price_max": None, "palabras_clave": ""},
            {"zona": "san isidro", "dormitorios": "0", "banos": "0", "price_min": None, "price_max": None, "palabras_clave": ""},
            {"zona": "santiago de surco", "dormitorios": "0", "banos": "0", "price_min": None, "price_max": None, "palabras_clave": ""},
        ]

    pool: List[dict] = []
    sections = []
    for q in base_queries:
        try:
            items = run_scrapers(**q)
            subset = items[:6]
            sections.append({"title": q["zona"].title(), "query": q, "count": len(subset), "properties": subset})
            pool.extend(items[:20])
        except Exception as e:
            logger.warning(f"home-feed sección falló para {q}: {e}")

    pool = dedupe_by_link(pool)
    scored = [(score_property(p), p) for p in pool]
    scored.sort(key=lambda t: t[0], reverse=True)
    featured = [p for _, p in scored[:9]]
    for p in featured:
        p["is_featured"] = True

    payload = {
        "featured": featured,
        "sections": sections,
        "generated_at": datetime.utcnow().isoformat(),
        "cached_ttl_minutes": int(HOME_CACHE_TTL.total_seconds() // 60),
    }
    set_home_cached(payload)
    return payload

# ─────────────────────────────────────────────────────────────
# Rutas de Admin, Públicas y "Seed"
# ─────────────────────────────────────────────────────────────
@app.get("/admin/ping", dependencies=[Depends(require_role(RoleEnum.ADMIN))])
def admin_ping(user=Depends(get_current_user)):
    return {"ok": True, "user": user.email, "role": user.role.value}

app.include_router(admin_router)
app.include_router(pub)

@app.post("/dev/seed-admin")
def seed_admin(db: DBSession = Depends(get_db)):
    if db.query(User).filter(User.email == "admin@local").first():
        return {"msg": "ya existe"}
    u = User(email="admin@local", password_hash=hash_password("admin123"), role=RoleEnum.ADMIN)
    db.add(u)
    db.commit()
    return {"ok": True, "email": u.email}