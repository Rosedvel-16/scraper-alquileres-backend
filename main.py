from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Tuple, Dict, Any
from datetime import datetime, timedelta
import logging
import threading
import re

from scraper import run_scrapers  # <-- tu scraper actual

# ----------------- Logging -----------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Scraper de Alquileres API", version="2.4.0")

# ----------------- CORS -----------------
FRONTEND_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "https://scraper-alquileres-frontend.vercel.app",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------- Paginación -----------------
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 20  # tope duro

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

def run_search(
    zona: str,
    dormitorios: str,
    banos: str,
    price_min: Optional[int],
    price_max: Optional[int],
    palabras_clave: str,
) -> List[dict]:
    results = run_scrapers(
        zona=zona,
        dormitorios=dormitorios,
        banos=banos,
        price_min=price_min,
        price_max=price_max,
        palabras_clave=palabras_clave
    )
    if results is None:
        return []
    if hasattr(results, "to_dict"):
        return results.to_dict("records")
    if isinstance(results, list):
        return results
    return []

# ----------------- Heurística de "destacado" -----------------
FEATURE_KEYWORDS = [
    "piscina", "mascota", "mascotas", "cochera", "estacionamiento",
    "terraza", "balcon", "ascensor", "gimnasio", "amoblado", "amoblada"
]

def score_property(p: Dict[str, Any]) -> float:
    """Más puntos si menciona amenities y si el precio (en S/) es menor."""
    score = 0.0
    text = f"{p.get('titulo','')} {p.get('descripcion','')}".lower()
    for kw in FEATURE_KEYWORDS:
        if kw in text:
            score += 1.0

    # precio en soles
    s = str(p.get("precio", ""))
    nums = re.sub(r"[^\d]", "", s)
    if s.strip().upper().startswith("S") and nums:
        try:
            val = int(nums)
            score += max(0.0, 3000.0 / max(val, 1))  # cuanto más bajo, mayor score
        except:
            pass

    # m2 (si lo hay)
    m2txt = str(p.get("m2", ""))
    m2m = re.search(r"(\d{1,4})", m2txt)
    if m2m:
        try:
            m2 = int(m2m.group(1))
            score += min(m2, 120) / 400.0
        except:
            pass
    return score

def mark_featured_one(items: List[dict]) -> List[dict]:
    """Marca exactamente 1 item con is_featured=True dentro de 'items'."""
    if not items:
        return items
    best_idx = None
    best_score = -1e9
    for i, p in enumerate(items):
        sc = score_property(p)
        if sc > best_score:
            best_score = sc
            best_idx = i
    for i, p in enumerate(items):
        p["is_featured"] = (i == best_idx)
    return items

def dedupe_by_link(items: List[dict]) -> List[dict]:
    seen = set()
    out = []
    for p in items:
        link = (p.get("link") or "").strip()
        key = link or (p.get("titulo","") + "|" + p.get("fuente",""))
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out

# ----------------- Métricas de búsquedas (trending) -----------------
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
    zona, dormitorios, banos, pmin, pmax, palabras = key.split("|")
    return {
        "zona": zona,
        "dormitorios": dormitorios if dormitorios else "0",
        "banos": banos if banos else "0",
        "price_min": int(pmin) if pmin else None,
        "price_max": int(pmax) if pmax else None,
        "palabras_clave": palabras or ""
    }

# ----------------- Cache simple con TTL para home-feed -----------------
HOME_CACHE: Dict[str, Any] = {"expires": datetime.min, "payload": None}
HOME_CACHE_TTL = timedelta(minutes=15)

def get_home_cached() -> Optional[dict]:
    if HOME_CACHE["payload"] and datetime.utcnow() < HOME_CACHE["expires"]:
        return HOME_CACHE["payload"]
    return None

def set_home_cached(payload: dict):
    HOME_CACHE["payload"] = payload
    HOME_CACHE["expires"] = datetime.utcnow() + HOME_CACHE_TTL

# ----------------- Rutas básicas -----------------
@app.get("/")
async def root():
    return {"message": "Scraper de Alquileres API", "status": "active"}

@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

@app.get("/sources")
async def list_sources():
    sources = ["nestoria", "infocasas", "urbania", "properati", "doomos"]
    return {"sources": sources}

# ----------------- Endpoints de búsqueda -----------------
@app.post("/search", response_model=SearchResponse)
async def search_properties_post(
    request: SearchRequest,
    page: int = Query(1, ge=1, description="Página 1-based"),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE, description="Tamaño de página (máx 20)")
):
    try:
        page = clamp_page(page)
        page_size = clamp_page_size(page_size)

        record_search(request.zona, request.dormitorios or "0", request.banos or "0",
                      request.price_min, request.price_max, request.palabras_clave or "")

        properties_all = run_search(
            zona=request.zona,
            dormitorios=request.dormitorios or "0",
            banos=request.banos or "0",
            price_min=request.price_min,
            price_max=request.price_max,
            palabras_clave=request.palabras_clave or ""
        )

        if not properties_all:
            empty_meta = PaginationMeta(page=1, page_size=page_size, total=0, total_pages=1, has_prev=False, has_next=False)
            return SearchResponse(success=True, count=0, properties=[], meta=empty_meta,
                                  message="No se encontraron propiedades que coincidan con los criterios")

        page_items, meta = paginate(properties_all, page, page_size)
        page_items = mark_featured_one(page_items)  # ✅ 1 destacado por página

        return SearchResponse(
            success=True,
            count=len(page_items),
            properties=page_items,
            meta=meta,
            message=f"Se encontraron {meta.total} propiedades"
        )
    except Exception as e:
        logger.exception("Error en búsqueda POST")
        raise HTTPException(status_code=500, detail=f"Error interno del servidor: {str(e)}")

@app.get("/search", response_model=SearchResponse)
async def search_properties_get(
    zona: str = Query(..., description="Zona a buscar"),
    dormitorios: str = Query("0", description="Dormitorios (0 = cualquiera)"),
    banos: str = Query("0", description="Baños (0 = cualquiera)"),
    price_min: Optional[int] = Query(None, description="Precio mínimo (S/)"),
    price_max: Optional[int] = Query(None, description="Precio máximo (S/)"),
    palabras_clave: str = Query("", description="Palabras clave (ej: 'piscina mascotas')"),
    page: int = Query(1, ge=1, description="Página 1-based"),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE, description="Tamaño de página (máx 20)")
):
    try:
        page = clamp_page(page)
        page_size = clamp_page_size(page_size)

        record_search(zona, dormitorios, banos, price_min, price_max, palabras_clave)

        properties_all = run_search(
            zona=zona,
            dormitorios=dormitorios,
            banos=banos,
            price_min=price_min,
            price_max=price_max,
            palabras_clave=palabras_clave
        )

        if not properties_all:
            empty_meta = PaginationMeta(page=1, page_size=page_size, total=0, total_pages=1, has_prev=False, has_next=False)
            return SearchResponse(success=True, count=0, properties=[], meta=empty_meta,
                                  message="No se encontraron propiedades que coincidan con los criterios")

        page_items, meta = paginate(properties_all, page, page_size)
        page_items = mark_featured_one(page_items)  # ✅ 1 destacado por página

        return SearchResponse(
            success=True,
            count=len(page_items),
            properties=page_items,
            meta=meta,
            message=f"Se encontraron {meta.total} propiedades"
        )
    except Exception as e:
        logger.exception("Error en búsqueda GET")
        raise HTTPException(status_code=500, detail=f"Error interno del servidor: {str(e)}")

# ----------------- Endpoint: consultas más buscadas -----------------
@app.get("/trending")
async def trending(limit: int = Query(6, ge=1, le=20)):
    """Devuelve combinaciones de búsqueda reales, ordenadas por frecuencia."""
    with STATS_LOCK:
        items = sorted(SEARCH_STATS.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    payload = []
    for key, count in items:
        parsed = parse_stats_key(key)
        parsed["count"] = count
        payload.append(parsed)
    return {"items": payload, "generated_at": datetime.utcnow().isoformat()}

# ----------------- Endpoint: home-feed (9 destacados reales) -----------------
@app.get("/home-feed")
async def home_feed():
    """
    Devuelve destacados reales para la portada.
    - Usa top 'trending' si existen; si no, zonas fallback.
    - Ejecuta búsquedas reales, crea un pool, lo deduplica y selecciona las 9 con mejor score.
    - Marca cada una con is_featured=True.
    - También devuelve 'sections' por compatibilidad con el front actual.
    """
    cached = get_home_cached()
    if cached:
        return cached

    # 1) Queries base
    with STATS_LOCK:
        sorted_stats = sorted(SEARCH_STATS.items(), key=lambda kv: kv[1], reverse=True)
    base_queries = [parse_stats_key(key) for key, _ in sorted_stats[:3]]

    if not base_queries:
        base_queries = [
            {"zona": "miraflores", "dormitorios": "0", "banos": "0", "price_min": None, "price_max": None, "palabras_clave": ""},
            {"zona": "san isidro", "dormitorios": "0", "banos": "0", "price_min": None, "price_max": None, "palabras_clave": ""},
            {"zona": "santiago de surco", "dormitorios": "0", "banos": "0", "price_min": None, "price_max": None, "palabras_clave": ""},
        ]

    # 2) Ejecutar búsquedas y armar pool + secciones
    pool: List[dict] = []
    sections = []
    for q in base_queries:
        try:
            items = run_search(
                zona=q["zona"],
                dormitorios=q["dormitorios"],
                banos=q["banos"],
                price_min=q["price_min"],
                price_max=q["price_max"],
                palabras_clave=q["palabras_clave"]
            )
            # limitar cada sección a 6 para no sobrecargar
            subset = items[:6]
            sections.append({
                "title": f"{q['zona'].title()}",
                "query": q,
                "count": len(subset),
                "properties": subset
            })
            pool.extend(items[:20])  # tomar hasta 20 por zona para el pool
        except Exception as e:
            logger.warning(f"home-feed sección falló para {q}: {e}")

    # 3) Dedup + score -> top 9
    pool = dedupe_by_link(pool)
    scored = [(score_property(p), p) for p in pool]
    scored.sort(key=lambda t: t[0], reverse=True)
    featured = [p for _, p in scored[:9]]
    for p in featured:
        p["is_featured"] = True

    payload = {
        "featured": featured,           # ✅ arreglo de 9 destacados
        "sections": sections,           # compatibilidad
        "generated_at": datetime.utcnow().isoformat(),
        "cached_ttl_minutes": int(HOME_CACHE_TTL.total_seconds() // 60),
    }
    set_home_cached(payload)
    return payload

# ----------------- Ejecución local -----------------
if __name__ == "__main__":
    import uvicorn
    print("🚀 Iniciando servidor FastAPI...")
    print("📍 URL: http://localhost:8000")
    print("📚 Documentación: http://localhost:8000/docs")
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
