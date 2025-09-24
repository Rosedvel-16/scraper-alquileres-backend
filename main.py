import os, sys
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from fastapi import FastAPI, HTTPException, Query, Depends, Request, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from typing import List, Optional, Tuple, Dict, Any
from datetime import datetime, timedelta
import logging
import threading
import re

# ----------------- Logging -----------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Scraper de Alquileres API", version="3.0.0")

# ----------------- CORS -----------------
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------- Config paginación -----------------
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 20

# ----------------- Modelos -----------------
class Property(BaseModel):
    id: str
    titulo: str
    precio: str
    m2: str
    dormitorios: str
    baños: str
    descripcion: str
    link: str
    fuente: str
    scraped_at: str
    imagen_url: str
    is_featured: Optional[bool] = False

class PaginationMeta(BaseModel):
    page: int
    page_size: int
    total: int
    total_pages: int
    has_prev: bool
    has_next: bool

class SearchRequest(BaseModel):
    zona: str
    dormitorios: Optional[str] = "0"
    banos: Optional[str] = "0"
    price_min: Optional[int] = None
    price_max: Optional[int] = None
    palabras_clave: Optional[str] = ""

class SearchResponse(BaseModel):
    success: bool
    count: int
    properties: List[Property]
    meta: PaginationMeta
    message: Optional[str] = None

# ----------------- Helpers -----------------
def clamp_page_size(page_size: Optional[int]) -> int:
    if page_size is None or page_size <= 0:
        return DEFAULT_PAGE_SIZE
    return min(page_size, MAX_PAGE_SIZE)

def clamp_page(page: Optional[int]) -> int:
    return 1 if (page is None or page <= 0) else page

def paginate(items: List[dict], page: int, page_size: int) -> Tuple[List[dict], PaginationMeta]:
    total = len(items)
    total_pages = max(1, (total + page_size - 1) // page_size)
    if page > total_pages:
        page = total_pages
    start = (page - 1) * page_size
    end = start + page_size
    slice_items = items[start:end]
    meta = PaginationMeta(
        page=page,
        page_size=page_size,
        total=total,
        total_pages=total_pages,
        has_prev=page > 1,
        has_next=page < total_pages
    )
    return slice_items, meta

# ----------------- Scraper fallback -----------------
try:
    from scraper import run_scrapers
except Exception:
    def run_scrapers(**kwargs):
        return []  # fallback seguro en Vercel

def run_search(**kwargs):
    try:
        results = run_scrapers(**kwargs)
        if results is None:
            return []
        if hasattr(results, "to_dict"):
            return results.to_dict("records")
        if isinstance(results, list):
            return results
        return []
    except Exception as e:
        logger.warning(f"Scraper fallback: {e}")
        return []

# ----------------- Destacados -----------------
FEATURE_KEYWORDS = ["piscina","mascota","mascotas","cochera","estacionamiento","terraza","balcon","ascensor","gimnasio","amoblado","amoblada"]

def score_property(p: Dict[str, Any]) -> float:
    score = 0.0
    text = f"{p.get('titulo','')} {p.get('descripcion','')}".lower()
    for kw in FEATURE_KEYWORDS:
        if kw in text:
            score += 1.0
    try:
        s = str(p.get("precio",""))
        nums = re.sub(r"[^\d]","",s)
        if s.strip().upper().startswith("S") and nums:
            val = int(nums)
            score += max(0.0, 3000.0/max(val,1))
    except:
        pass
    try:
        m2txt = str(p.get("m2",""))
        m2m = re.search(r"(\d{1,4})", m2txt)
        if m2m:
            m2 = int(m2m.group(1))
            score += min(m2,120)/400.0
    except:
        pass
    return score

def mark_featured_one(items: List[dict]) -> List[dict]:
    if not items:
        return items
    best_idx = None
    best_score = -1e9
    for i,p in enumerate(items):
        sc = score_property(p)
        if sc > best_score:
            best_score = sc
            best_idx = i
    for i,p in enumerate(items):
        p["is_featured"] = (i == best_idx)
    return items

def dedupe_by_link(items: List[dict]) -> List[dict]:
    seen = set()
    out = []
    for p in items:
        link = (p.get("link") or "").strip()
        key = link or (p.get("titulo","")+"|"+p.get("fuente",""))
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out

# ----------------- Métricas -----------------
SEARCH_STATS: Dict[str,int] = {}
STATS_LOCK = threading.Lock()

def _stats_key(zona:str,dormitorios:str,banos:str,price_min:Optional[int],price_max:Optional[int],palabras_clave:str)->str:
    return f"{zona}|{dormitorios}|{banos}|{price_min}|{price_max}|{palabras_clave}"

def record_search(zona,dormitorios,banos,price_min,price_max,palabras_clave):
    key = _stats_key(zona,dormitorios,banos,price_min,price_max,palabras_clave)
    with STATS_LOCK:
        SEARCH_STATS[key] = SEARCH_STATS.get(key,0)+1

def parse_stats_key(key:str)->Dict[str,Any]:
    zona,dormitorios,banos,pmin,pmax,palabras = key.split("|")
    return {
        "zona": zona,
        "dormitorios": dormitorios if dormitorios else "0",
        "banos": banos if banos else "0",
        "price_min": int(pmin) if pmin else None,
        "price_max": int(pmax) if pmax else None,
        "palabras_clave": palabras or ""
    }

# ----------------- Función interna para POST y GET -----------------
def _search_logic(zona:str,dormitorios:str,banos:str,price_min:Optional[int],price_max:Optional[int],palabras_clave:str,page:int=1,page_size:int=DEFAULT_PAGE_SIZE):
    page = clamp_page(page)
    page_size = clamp_page_size(page_size)
    record_search(zona,dormitorios,banos,price_min,price_max,palabras_clave)
    props = run_search(
        zona=zona,dormitorios=dormitorios,banos=banos,
        price_min=price_min,price_max=price_max,palabras_clave=palabras_clave
    )
    props = mark_featured_one(dedupe_by_link(props))
    page_items, meta = paginate(props, page, page_size)
    return SearchResponse(
        success=True,
        count=len(page_items),
        properties=page_items,
        meta=meta,
        message=f"Se encontraron {len(props)} propiedades"
    )

# ----------------- Endpoints POST y GET -----------------
@app.post("/search", response_model=SearchResponse)
async def search_properties_post(request:SearchRequest, page:int=Query(1), page_size:int=Query(DEFAULT_PAGE_SIZE)):
    return _search_logic(
        zona=request.zona,
        dormitorios=request.dormitorios or "0",
        banos=request.banos or "0",
        price_min=request.price_min,
        price_max=request.price_max,
        palabras_clave=request.palabras_clave or "",
        page=page,
        page_size=page_size
    )

@app.get("/search", response_model=SearchResponse)
async def search_properties_get(
    zona:str=Query(...),
    dormitorios:str=Query("0"),
    banos:str=Query("0"),
    price_min:Optional[int]=Query(None),
    price_max:Optional[int]=Query(None),
    palabras_clave:str=Query(""),
    page:int=Query(1),
    page_size:int=Query(DEFAULT_PAGE_SIZE)
):
    return _search_logic(
        zona=zona,
        dormitorios=dormitorios,
        banos=banos,
        price_min=price_min,
        price_max=price_max,
        palabras_clave=palabras_clave,
        page=page,
        page_size=page_size
    )

# ----------------- Otros endpoints como /trending, /home-feed, admin, routers ... siguen igual -----------------
# (Incluye admin_router, pub, seed_admin, etc., como en tu main.py original)

# ----------------- Uvicorn local -----------------
if __name__=="__main__":
    import uvicorn
    print("🚀 Iniciando servidor FastAPI...")
    print("📍 URL: http://localhost:8000")
    print("📚 Documentación: http://localhost:8000/docs")
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
