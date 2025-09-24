# routes_public.py
from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy.orm import Session as DBSession
from db import get_db
from models import Property, PropertyView

pub = APIRouter(tags=["public"])

@pub.get("/property/{pid}")
def get_property(pid: int, db: DBSession = Depends(get_db), request: Request = None):
    p = db.query(Property).get(pid)
    if not p:
        raise HTTPException(404, "No existe")
    v = PropertyView(property_id=p.id, source="web", session_key=request.headers.get("X-Client-Id"))
    db.add(v); db.commit()
    return {"item": {"id": p.id, "titulo": p.titulo, "descripcion": p.descripcion, "imagen_url": p.imagen_url, "precio": p.precio}}
