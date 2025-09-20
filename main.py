from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Tuple
from datetime import datetime
import logging

# Import perezoso dentro de la función, pero dejamos el import aquí si prefieres eager:
from scraper import run_scrapers 

# ----------------- Logging -----------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Scraper de Alquileres API", version="2.2.1")

# ----------------- CORS -----------------
FRONTEND_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "https://scraper-alquileres-frontend.vercel.app",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
    allow_origin_regex=r"https://.*\.vercel\.app",  # para previas de Vercel
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------- Paginación -----------------
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 20  # 🔒 tope duro de 20 por página

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
    count: int                      # elementos en esta página
    properties: List[Property]      # subset paginado
    meta: PaginationMeta            # metadatos globales
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
    # si piden página > total_pages, se ajusta a la última
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
    """Ejecuta los scrapers y normaliza a list[dict]."""
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

    # Soportar DataFrame o lista de dicts
    if hasattr(results, "to_dict"):
        return results.to_dict("records")
    if isinstance(results, list):
        return results
    return []

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

# ----------------- Endpoints de búsqueda (con paginación) -----------------
@app.post("/search", response_model=SearchResponse)
async def search_properties(
    request: SearchRequest,
    page: int = Query(1, ge=1, description="Página 1-based"),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE, description="Tamaño de página (máx 20)")
):
    try:
        page = clamp_page(page)
        page_size = clamp_page_size(page_size)

        properties_all = run_search(
            zona=request.zona,
            dormitorios=request.dormitorios or "0",
            banos=request.banos or "0",
            price_min=request.price_min,
            price_max=request.price_max,
            palabras_clave=request.palabras_clave or ""
        )

        if not properties_all:
            empty_meta = PaginationMeta(
                page=1, page_size=page_size, total=0, total_pages=1, has_prev=False, has_next=False
            )
            return SearchResponse(success=True, count=0, properties=[], meta=empty_meta,
                                  message="No se encontraron propiedades que coincidan con los criterios")

        page_items, meta = paginate(properties_all, page, page_size)

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
    zona: str = Query(..., description="Zona a buscar (ej: miraflores, san isidro)"),
    dormitorios: str = Query("0", description="Número de dormitorios (0 para cualquier)"),
    banos: str = Query("0", description="Número de baños (0 para cualquier)"),
    price_min: Optional[int] = Query(None, description="Precio mínimo en soles"),
    price_max: Optional[int] = Query(None, description="Precio máximo en soles"),
    palabras_clave: str = Query("", description="Palabras clave para filtrar (ej: 'piscina mascotas')"),
    page: int = Query(1, ge=1, description="Página 1-based"),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE, description="Tamaño de página (máx 20)")
):
    try:
        page = clamp_page(page)
        page_size = clamp_page_size(page_size)

        properties_all = run_search(
            zona=zona,
            dormitorios=dormitorios,
            banos=banos,
            price_min=price_min,
            price_max=price_max,
            palabras_clave=palabras_clave
        )

        if not properties_all:
            empty_meta = PaginationMeta(
                page=1, page_size=page_size, total=0, total_pages=1, has_prev=False, has_next=False
            )
            return SearchResponse(success=True, count=0, properties=[], meta=empty_meta,
                                  message="No se encontraron propiedades que coincidan con los criterios")

        page_items, meta = paginate(properties_all, page, page_size)

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

# ----------------- Ejecución local -----------------
if __name__ == "__main__":
    import uvicorn
    print("🚀 Iniciando servidor FastAPI...")
    print("📍 URL: http://localhost:8000")
    print("📚 Documentación: http://localhost:8000/docs")
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        reload=True
    )
