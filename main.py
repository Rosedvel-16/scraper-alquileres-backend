from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
import logging
from scraper import run_scrapers

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Scraper de Alquileres API", version="1.0.0")

# Configurar CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
    imagen_url: str  # 👈 Campo para la URL de la imagen

class SearchRequest(BaseModel):
    zona: str
    dormitorios: Optional[str] = "0"
    banos: Optional[str] = "0"
    price_min: Optional[int] = None
    price_max: Optional[int] = None
    palabras_clave: Optional[str] = ""  # 👈 Nuevo campo para palabras clave

class SearchResponse(BaseModel):
    success: bool
    count: int
    properties: List[Property]
    message: Optional[str] = None

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

@app.post("/search", response_model=SearchResponse)
async def search_properties(request: SearchRequest):
    try:
        results = run_scrapers(
            zona=request.zona,
            dormitorios=request.dormitorios,
            banos=request.banos,
            price_min=request.price_min,
            price_max=request.price_max,
            palabras_clave=request.palabras_clave  # 👈 Pasar palabras clave
        )
        
        if results.empty:
            return SearchResponse(
                success=True,
                count=0,
                properties=[],
                message="No se encontraron propiedades que coincidan con los criterios"
            )
        
        properties = results.to_dict('records')
        
        return SearchResponse(
            success=True,
            count=len(properties),
            properties=properties,
            message=f"Se encontraron {len(properties)} propiedades"
        )
        
    except Exception as e:
        logger.error(f"Error en búsqueda: {e}")
        raise HTTPException(status_code=500, detail=f"Error interno del servidor: {str(e)}")

@app.get("/search", response_model=SearchResponse)
async def search_properties_get(
    zona: str = Query(..., description="Zona a buscar (ej: miraflores, san isidro)"),
    dormitorios: str = Query("0", description="Número de dormitorios (0 para cualquier)"),
    banos: str = Query("0", description="Número de baños (0 para cualquier)"),
    price_min: Optional[int] = Query(None, description="Precio mínimo en soles"),
    price_max: Optional[int] = Query(None, description="Precio máximo en soles"),
    palabras_clave: str = Query("", description="Palabras clave para filtrar (ej: 'piscina mascotas')")
):
    try:
        results = run_scrapers(
            zona=zona,
            dormitorios=dormitorios,
            banos=banos,
            price_min=price_min,
            price_max=price_max,
            palabras_clave=palabras_clave  # 👈 Pasar palabras clave
        )
        
        if results.empty:
            return SearchResponse(
                success=True,
                count=0,
                properties=[],
                message="No se encontraron propiedades que coincidan con los criterios"
            )
        
        properties = results.to_dict('records')
        
        return SearchResponse(
            success=True,
            count=len(properties),
            properties=properties,
            message=f"Se encontraron {len(properties)} propiedades"
        )
        
    except Exception as e:
        logger.error(f"Error en búsqueda GET: {e}")
        raise HTTPException(status_code=500, detail=f"Error interno del servidor: {str(e)}")

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