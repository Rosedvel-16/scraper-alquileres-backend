# auth.py
import os
from datetime import datetime, timedelta
from typing import Tuple
from fastapi import Depends, HTTPException, status, Body
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from passlib.context import CryptContext
from sqlalchemy.orm import Session as DBSession

from models import User, RoleEnum
from db import get_db
from pydantic import BaseModel

# Configuración
JWT_SECRET = os.getenv("JWT_SECRET", "cambia-esto")
JWT_ALG = os.getenv("JWT_ALG", "HS256")
ACCESS_TTL_MIN = int(os.getenv("ACCESS_TTL_MIN", "30"))

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

class LoginPayload(BaseModel):
    email: str
    password: str

def verify_password(p: str, h: str) -> bool:
    return pwd_ctx.verify(p, h)

def create_access_token(user: User) -> str:
    now = datetime.utcnow()
    access_payload = {
        "sub": str(user.id),
        "role": user.role.value,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=ACCESS_TTL_MIN),
    }
    return jwt.encode(access_payload, JWT_SECRET, algorithm=JWT_ALG)

def get_current_user(db: DBSession = Depends(get_db), token: str = Depends(oauth2_scheme)) -> User:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Token inválido")
    except JWTError:
        raise HTTPException(status_code=401, detail="No autenticado")

    user = db.query(User).filter(User.id == int(user_id), User.is_active == True).first()
    if not user:
        raise HTTPException(status_code=401, detail="Usuario no encontrado o inactivo")
    
    return user

def require_role(*roles: RoleEnum):
    def dep(user: User = Depends(get_current_user)):
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="No autorizado")
        return user
    return dep

def login_handler(db: DBSession, payload: LoginPayload = Body(...)):
    user = db.query(User).filter(User.email == payload.email).first()
    if not user or not verify_password(payload.password, user.password_hash) or not user.is_active:
        raise HTTPException(status_code=400, detail="Credenciales inválidas")
    
    token = create_access_token(user)
    return {"access_token": token, "token_type": "bearer", "role": user.role.value}