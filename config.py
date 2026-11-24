"""全局配置"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ===== Milvus =====
    milvus_uri: str = "http://localhost:19530"
    milvus_db_name: str = "csagent"
    collection_name: str = "knowledge_base"

    # ===== Embedding =====
    embedding_provider: str = "api"          # api | local
    embedding_model: str = "BAAI/bge-m3"
    embedding_dim: int = 1024                # bge-m3 是 1024
    embedding_api_key: str = ""
    embedding_base_url: str = "https://api.siliconflow.cn/v1"
    embedding_batch_size: int = 16

    # ===== LLM（DeepSeek）=====
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-chat"
    llm_temperature: float = 0.3

    # ===== PostgreSQL =====
    database_url: str = (
        "postgresql+psycopg2://csagent:csagent123@localhost:5432/csagent"
    )
    db_echo: bool = False

    # ===== RAG =====
    kb_dir: Path = BASE_DIR / "data" / "knowledge_base"
    chunk_size: int = 500
    chunk_overlap: int = 50
    top_k: int = 3
    # 检索结果相关度阈值（COSINE，低于该值视为知识库未覆盖）
    rag_score_threshold: float = 0.35

    # ===== 记忆 =====
    short_term_window: int = 6
    long_term_top_n: int = 3

    # ===== 登录会话 =====
    secret_key: str = "cs-agent-dev-secret-change-in-production"
    session_max_age: int = 7 * 24 * 3600

settings = Settings()