# core/auth_models.py
from datetime import datetime, timedelta
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from werkzeug.security import generate_password_hash, check_password_hash
from core.db import Base

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, index=True, nullable=False)
    pw_hash = Column(String, nullable=False)
    tenant_id = Column(Integer, index=True)          # empresa/organização
    is_admin = Column(Boolean, default=False)

    plan = Column(String, default="free")            # free | basic | pro | enterprise
    active_until = Column(DateTime, nullable=True)   # data fim de assinatura
    created_at = Column(DateTime, default=datetime.utcnow)

    def set_password(self, raw: str):
        self.pw_hash = generate_password_hash(raw)

    def check_password(self, raw: str) -> bool:
        return check_password_hash(self.pw_hash, raw)

    # Flask-Login hooks
    @property
    def is_authenticated(self): return True
    @property
    def is_active(self): return True
    @property
    def is_anonymous(self): return False
    def get_id(self): return str(self.id)

    def has_active_subscription(self) -> bool:
        return self.active_until and self.active_until >= datetime.utcnow()
