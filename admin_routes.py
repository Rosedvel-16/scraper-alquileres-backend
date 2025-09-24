# admin_routes.py
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, Query
from sqlalchemy.orm import Session as DBSession
import csv, io, json
from datetime import datetime, timedelta
from sqlalchemy import func

from db import get_db
from models import Property, PropertyView, AuditLog, RoleEnum
from auth import require_role, get_current_user
from pydantic import BaseModel

router = APIRouter(prefix="/admin", tags=["admin"])

class PropertyIn(BaseModel):
    titulo: str
    precio: str | None = None
    moneda: str | None = None
    m2: float | None = None
    dormitorios: str | None = None
    banos: str | None = None
    descripcion: str | None = None
    link: str | None = None
    imagen_url: str | None = None
    fuente: str | None = None
    distrito: str | None = None
    activo: bool = True
    external_id: str | None = None

@router.get("/properties", dependencies=[Depends(require_role(RoleEnum.ADMIN, RoleEnum.EDITOR, RoleEnum.VIEWER))])
def list_properties(db: DBSession = Depends(get_db), q: str = "", page: int = 1, size: int = 20):
    qs = db.query(Property)
    if q:
        like = f"%{q}%"
        qs = qs.filter(Property.titulo.ilike(like))
    total = qs.count()
    items = qs.order_by(Property.updated_at.desc()).offset((page-1)*size).limit(size).all()
    return {"total": total, "items": [serialize_prop(p) for p in items]}

@router.post("/properties", dependencies=[Depends(require_role(RoleEnum.ADMIN, RoleEnum.EDITOR))])
def create_property(data: PropertyIn, db: DBSession = Depends(get_db), user=Depends(get_current_user)):
    p = Property(**data.dict())
    db.add(p); db.commit(); db.refresh(p)
    log_audit(db, user.id, "Property", str(p.id), "CREATE", None, serialize_prop(p))
    return serialize_prop(p)

@router.put("/properties/{prop_id}", dependencies=[Depends(require_role(RoleEnum.ADMIN, RoleEnum.EDITOR))])
def update_property(prop_id: int, data: PropertyIn, db: DBSession = Depends(get_db), user=Depends(get_current_user)):
    p = db.query(Property).get(prop_id)
    if not p: raise HTTPException(404, "No existe")
    before = serialize_prop(p)
    for k,v in data.dict().items():
        setattr(p, k, v)
    p.updated_at = datetime.utcnow()
    db.commit(); db.refresh(p)
    log_audit(db, user.id, "Property", str(prop_id), "UPDATE", before, serialize_prop(p))
    return serialize_prop(p)

@router.delete("/properties/{prop_id}", dependencies=[Depends(require_role(RoleEnum.ADMIN))])
def delete_property(prop_id: int, db: DBSession = Depends(get_db), user=Depends(get_current_user)):
    p = db.query(Property).get(prop_id)
    if not p: raise HTTPException(404, "No existe")
    before = serialize_prop(p)
    db.delete(p); db.commit()
    log_audit(db, user.id, "Property", str(prop_id), "DELETE", before, None)
    return {"ok": True}

@router.post("/import", dependencies=[Depends(require_role(RoleEnum.ADMIN, RoleEnum.EDITOR))])
async def import_properties(file: UploadFile = File(...), db: DBSession = Depends(get_db), user=Depends(get_current_user)):
    content = await file.read()
    filename = file.filename.lower()
    created, updated = 0, 0

    if filename.endswith(".csv"):
        sio = io.StringIO(content.decode("utf-8"))
        reader = csv.DictReader(sio)
        rows = list(reader)
    elif filename.endswith(".json"):
        rows = json.loads(content.decode("utf-8"))
        if isinstance(rows, dict): rows = rows.get("items", [])
    else:
        raise HTTPException(400, "Formato no soportado (usa .csv o .json)")

    for r in rows:
        ext_id = (r.get("external_id") or r.get("id") or "").strip() or None
        p = None
        if ext_id:
            p = db.query(Property).filter(Property.external_id == ext_id).first()

        payload = map_row_to_property(r)

        if p:
            before = serialize_prop(p)
            for k,v in payload.items():
                setattr(p, k, v)
            p.updated_at = datetime.utcnow()
            db.commit(); db.refresh(p)
            log_audit(db, user.id, "Property", str(p.id), "IMPORT", before, serialize_prop(p))
            updated += 1
        else:
            p = Property(**payload)
            db.add(p); db.commit(); db.refresh(p)
            log_audit(db, user.id, "Property", str(p.id), "IMPORT", None, serialize_prop(p))
            created += 1

    return {"created": created, "updated": updated}

@router.get("/stats/top", dependencies=[Depends(require_role(RoleEnum.ADMIN, RoleEnum.EDITOR, RoleEnum.VIEWER))])
def top_stats(db: DBSession = Depends(get_db),
              window: str = Query("7d", description="rango: 24h, 7d, 30d, total"),
              limit: int = 5):
    now = datetime.utcnow()
    if window == "24h":
        since = now - timedelta(hours=24)
    elif window == "7d":
        since = now - timedelta(days=7)
    elif window == "30d":
        since = now - timedelta(days=30)
    else:
        since = None

    sub = db.query(
        PropertyView.property_id.label("pid"),
        func.count(PropertyView.id).label("views")
    )
    if since:
        sub = sub.filter(PropertyView.viewed_at >= since)
    sub = sub.group_by(PropertyView.property_id).subquery()

    rows = db.query(Property, sub.c.views).join(sub, sub.c.pid == Property.id).order_by(sub.c.views.desc()).limit(limit).all()
    return [{"property": serialize_prop(r[0]), "views": int(r[1])} for r in rows]

def serialize_prop(p: Property) -> dict:
    return {
        "id": p.id, "external_id": p.external_id, "titulo": p.titulo, "precio": p.precio,
        "moneda": p.moneda, "m2": p.m2, "dormitorios": p.dormitorios, "banos": p.banos,
        "descripcion": p.descripcion, "link": p.link, "imagen_url": p.imagen_url,
        "fuente": p.fuente, "distrito": p.distrito, "activo": p.activo,
        "created_at": p.created_at.isoformat(), "updated_at": p.updated_at.isoformat() if p.updated_at else None
    }

def map_row_to_property(r: dict) -> dict:
    return {
        "external_id": (r.get("external_id") or r.get("id")),
        "titulo": r.get("titulo") or r.get("title") or "",
        "precio": r.get("precio") or r.get("price"),
        "moneda": r.get("moneda") or r.get("currency"),
        "m2": safe_float(r.get("m2") or r.get("area")),
        "dormitorios": r.get("dormitorios") or r.get("bedrooms"),
        "banos": r.get("banos") or r.get("bathrooms"),
        "descripcion": r.get("descripcion") or r.get("description"),
        "link": r.get("link") or r.get("url"),
        "imagen_url": r.get("imagen_url") or r.get("image"),
        "fuente": r.get("fuente") or r.get("source"),
        "distrito": r.get("distrito") or r.get("district"),
        "activo": str(r.get("activo")).lower() not in ("false","0","none","null"),
    }

def safe_float(x):
    try:
        if x is None: return None
        return float(str(x).replace(",", "."))
    except:
        return None

def log_audit(db: DBSession, user_id: int | None, entity: str, entity_id: str, action: str, before: dict|None, after: dict|None):
    entry = AuditLog(
        user_id=user_id, entity=entity, entity_id=entity_id, action=action,
        before=json.dumps(before, ensure_ascii=False) if before else None,
        after=json.dumps(after, ensure_ascii=False) if after else None,
    )
    db.add(entry); db.commit()
