# models.py
from db import Base
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, DateTime, Boolean, ForeignKey, Text, Enum, Float
)
from sqlalchemy.orm import relationship
import enum

Base = declarative_base()

class RoleEnum(str, enum.Enum):
    ADMIN = "ADMIN"
    EDITOR = "EDITOR"
    VIEWER = "VIEWER"

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String(160), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(Enum(RoleEnum), default=RoleEnum.VIEWER, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    sessions = relationship("Session", back_populates="user")

class Session(Base):
    __tablename__ = "sessions"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    refresh_jti = Column(String(64), unique=True, index=True, nullable=False)
    user_agent = Column(String(255))
    ip = Column(String(64))
    is_revoked = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)

    user = relationship("User", back_populates="sessions")

class Property(Base):
    __tablename__ = "properties"
    id = Column(Integer, primary_key=True)
    external_id = Column(String(120), index=True)
    titulo = Column(String(255), nullable=False)
    precio = Column(String(64))
    moneda = Column(String(8))
    m2 = Column(Float)
    dormitorios = Column(String(16))
    banos = Column(String(16))
    descripcion = Column(Text)
    link = Column(Text)
    imagen_url = Column(Text)
    fuente = Column(String(64))
    distrito = Column(String(64))
    activo = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class PropertyView(Base):
    __tablename__ = "property_views"
    id = Column(Integer, primary_key=True)
    property_id = Column(Integer, ForeignKey("properties.id"), nullable=False)
    viewed_at = Column(DateTime, default=datetime.utcnow, index=True)
    source = Column(String(64))
    session_key = Column(String(64))

class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    entity = Column(String(64))
    entity_id = Column(String(64))
    action = Column(String(32))
    before = Column(Text)
    after = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
