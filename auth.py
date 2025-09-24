# auth.py
import os, uuid
from datetime import datetime, timedelta
from typing import Tuple
from fastapi import Depends, HTTPException, status, Request, Body
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import jwt, JWTError
from passlib.context import CryptContext
from sqlalchemy.orm import Session as DBSession

from models import User, Session, RoleEnum
from db import get_db

JWT_SECRET = os.getenv("JWT_SECRET", "cambia-esto")
JWT_ALG = os.getenv("JWT_ALG", "HS256")
ACCESS_TTL_MIN = int(os.getenv("ACCESS_TTL_MIN", "30"))
REFRESH_TTL_DAYS = int(os.getenv("REFRESH_TTL_DAYS", "15"))

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

def hash_password(p: str) -> str:
    return pwd_ctx.hash(p)

def verify_password(p: str, h: str) -> bool:
    return pwd_ctx.verify(p, h)

def create_tokens(user: User, refresh_jti: str) -> Tuple[str, str]:
    now = datetime.utcnow()
    access_payload = {
        "sub": str(user.id), "role": user.role.value, "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=ACCESS_TTL_MIN)).timestamp()),
    }
    refresh_payload = {
        "sub": str(user.id), "role": user.role.value, "type": "refresh",
        "jti": refresh_jti, "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=REFRESH_TTL_DAYS)).timestamp()),
    }
    return (
        jwt.encode(access_payload, JWT_SECRET, algorithm=JWT_ALG),
        jwt.encode(refresh_payload, JWT_SECRET, algorithm=JWT_ALG),
    )

def decode_token(token: str) -> dict:
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])

def get_current_user(db: DBSession = Depends(get_db), token: str = Depends(oauth2_scheme)) -> User:
    try:
        payload = decode_token(token)
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Token inválido")
        user_id = int(payload["sub"])
    except JWTError:
        raise HTTPException(status_code=401, detail="No autenticado")

    user = db.query(User).filter(User.id == user_id, User.is_active == True).first()
    if not user:
        raise HTTPException(status_code=401, detail="Usuario no encontrado o inactivo")
    return user

def require_role(*roles: RoleEnum):
    def dep(user: User = Depends(get_current_user)):
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="No autorizado")
        return user
    return dep

def login_handler(request: Request, form: OAuth2PasswordRequestForm, db: DBSession):
    user = db.query(User).filter(User.email == form.username).first()
    if not user or not verify_password(form.password, user.password_hash) or not user.is_active:
        raise HTTPException(status_code=400, detail="Credenciales inválidas")
    jti = uuid.uuid4().hex
    refresh_expires = datetime.utcnow() + timedelta(days=REFRESH_TTL_DAYS)
    sess = Session(
        user_id=user.id, refresh_jti=jti,
        user_agent=request.headers.get("user-agent"),
        ip=request.client.host if request.client else None,
        expires_at=refresh_expires
    )
    db.add(sess); db.commit()
    access, refresh = create_tokens(user, jti)
    return {"access_token": access, "refresh_token": refresh, "token_type": "bearer", "role": user.role.value}

def refresh_handler(db: DBSession, refresh_token: str):
    try:
        payload = decode_token(refresh_token)
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Refresh inválido")
        user_id = int(payload["sub"]); jti = payload["jti"]
    except JWTError:
        raise HTTPException(status_code=401, detail="Refresh inválido")

    sess = db.query(Session).filter(Session.refresh_jti == jti, Session.is_revoked == False).first()
    if not sess or sess.expires_at < datetime.utcnow():
        raise HTTPException(status_code=401, detail="Sesión expirada")

    user = db.query(User).filter(User.id == user_id, User.is_active == True).first()
    if not user:
        raise HTTPException(status_code=401, detail="Usuario no válido")

    access, new_refresh = create_tokens(user, jti)
    return {"access_token": access, "refresh_token": new_refresh, "token_type": "bearer", "role": user.role.value}

def logout_handler(db: DBSession, refresh_token: str):
    try:
        payload = decode_token(refresh_token)
        jti = payload["jti"]
    except Exception:
        raise HTTPException(status_code=400, detail="Refresh inválido")

    updated = db.query(Session).filter(Session.refresh_jti == jti).update({"is_revoked": True})
    db.commit()
    if not updated:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")
    return {"ok": True}
