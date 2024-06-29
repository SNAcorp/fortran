from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from database import Base
from datetime import datetime

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    is_active = Column(Integer, default=0)  # 0 - ожидание, 1 - доступ разрешен, 2 - доступ запрещен
    is_admin = Column(Boolean, default=False)
    registration_date = Column(DateTime, default=datetime.utcnow)

    files = relationship("File", back_populates="owner")

class File(Base):
    __tablename__ = "files"

    id = Column(Integer, primary_key=True, index=True)
    original_filename = Column(String)
    modified_filename = Column(String)
    title = Column(String)
    description = Column(String)
    is_public = Column(Boolean, default=False)
    owner_id = Column(Integer, ForeignKey("users.id"))
    upload_date = Column(DateTime, default=datetime.utcnow)
    hashtags = Column(String)
    download_count = Column(Integer, default=0)
    modifier_version_id = Column(Integer, ForeignKey("modifier_versions.id"))
    status = Column(String, default="waiting")  # New field for status

    owner = relationship("User", back_populates="files")
    modifier_version = relationship("ModifierVersion")

class ModifierVersion(Base):
    __tablename__ = "modifier_versions"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    file_path = Column(String)
    upload_date = Column(DateTime, default=datetime.utcnow)