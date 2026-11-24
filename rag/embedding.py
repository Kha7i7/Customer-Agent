"""Embedding 封装：支持硅基流动 API 和本地 sentence-transformers 两种模式"""
from config import settings

class EmbeddingModel:
    """建库和检索共用，维度由 settings.embedding_dim 决定"""

    _local_model = None
    _api_client = None

    def __init__(self):
        self.provider = settings.embedding_provider.lower()

        if self.provider == "api":
            if EmbeddingModel._api_client is None:
                from openai import OpenAI
                EmbeddingModel._api_client = OpenAI(
                    api_key=settings.embedding_api_key,
                    base_url=settings.embedding_base_url,
                )
            self.client = EmbeddingModel._api_client
        else:
            if EmbeddingModel._local_model is None:
                from sentence_transformers import SentenceTransformer
                EmbeddingModel._local_model = SentenceTransformer(settings.embedding_model)
            self.model = EmbeddingModel._local_model

    @staticmethod
    def _normalize(vec: list[float]) -> list[float]:
        """L2 归一化，配合 COSINE 使用"""
        norm = sum(x * x for x in vec) ** 0.5 or 1.0
        return [x / norm for x in vec]

    def encode_documents(self, texts: list[str]) -> list[list[float]]:
        """文档向量化"""
        if not texts:
            return []

        if self.provider == "api":
            batch = settings.embedding_batch_size
            vectors = []
            for start in range(0, len(texts), batch):
                resp = self.client.embeddings.create(
                    model=settings.embedding_model,
                    input=texts[start:start + batch],
                )
                vectors.extend(self._normalize(item.embedding) for item in resp.data)
            return vectors

        vectors = self.model.encode(
            texts,
            batch_size=settings.embedding_batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return vectors.tolist()

    def encode_query(self, text: str) -> list[float]:
        """查询向量化。bge-m3 不需要指令前缀"""
        return self.encode_documents([text])[0]