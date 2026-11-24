"""数据库连接：引擎、会话工厂、ORM 基类"""
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from config import settings

class Base(DeclarativeBase):
    """所有 ORM 模型的基类"""

engine = create_engine(
    settings.database_url,
    echo=settings.db_echo,
    pool_pre_ping=True,      # 断线自动重连，避免长时间空闲后报错
    pool_size=5,
    max_overflow=10,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

def get_db():
    """FastAPI 依赖注入用"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()