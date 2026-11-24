"""Milvus 封装：建库、建集合、写入、检索、统计"""
from pymilvus import DataType, MilvusClient

from config import settings

class VectorStore:
    def __init__(self):
        self.client = MilvusClient(uri=settings.milvus_uri)
        self.db_name = settings.milvus_db_name
        self.collection = settings.collection_name
        self.dim = settings.embedding_dim

        self._ensure_database()
        self._ensure_collection()

    # ---------- 建库 ----------
    def _ensure_database(self):
        if self.db_name not in self.client.list_databases():
            self.client.create_database(db_name=self.db_name)
        self.client.use_database(self.db_name)

    # ---------- 建集合 ----------
    def _ensure_collection(self):
        if self.client.has_collection(self.collection):
            return

        schema = MilvusClient.create_schema(
            auto_id=False,              # 主键自己指定，方便和原文对应
            enable_dynamic_field=False, # 字段固定，写错字段会直接报错
        )
        schema.add_field("id", DataType.INT64, is_primary=True)
        schema.add_field("vector", DataType.FLOAT_VECTOR, dim=self.dim)
        schema.add_field("text", DataType.VARCHAR, max_length=65535)
        schema.add_field("source", DataType.VARCHAR, max_length=512)

        index_params = self.client.prepare_index_params()
        index_params.add_index(
            field_name="vector",
            index_type="AUTOINDEX",     # 让 Milvus 自动选索引
            metric_type="COSINE",       # 余弦相似度
        )
        self.client.create_collection(
            collection_name=self.collection,
            schema=schema,
            index_params=index_params,
        )

    # ---------- 写入 ----------
    def insert(self, records: list[dict]) -> int:
        """records: [{"id": 1, "vector": [...], "text": "...", "source": "..."}]"""
        res = self.client.insert(collection_name=self.collection, data=records)
        self.client.flush(self.collection)   # 立即落盘，否则统计可能查不到
        return res["insert_count"]

    # ---------- 检索 ----------
    def search(self, vector: list[float], top_k: int | None = None) -> list[dict]:
        results = self.client.search(
            collection_name=self.collection,
            data=[vector],
            limit=top_k or settings.top_k,
            output_fields=["text", "source"],
        )

        hits = []
        for hit in results[0]:
            hits.append({
                "text": hit["entity"]["text"],
                "source": hit["entity"]["source"],
                "score": hit["distance"],   # COSINE 下是相似度，越大越相关
            })
        return hits

    # ---------- 管理 ----------
    def count(self) -> int:
        stats = self.client.get_collection_stats(self.collection)
        return int(stats.get("row_count", 0))

    def has_data(self) -> bool:
        return self.count() > 0

    def delete_by_source(self, source: str) -> int:
        """按文件名删除，便于文档更新时重建"""
        res = self.client.delete(
            collection_name=self.collection,
            filter=f'source == "{source}"',
        )
        self.client.flush(self.collection)
        return res.get("delete_count", 0)

    def drop(self):
        if self.client.has_collection(self.collection):
            self.client.drop_collection(self.collection)

# 冒烟测试：直接右键 Run 这个文件
if __name__ == "__main__":
    store = VectorStore()
    print("数据库列表:", store.client.list_databases())
    print("当前集合:", store.client.list_collections())
    print(f"集合 {store.collection} 行数:", store.count())