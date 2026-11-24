"""检索器：查询向量化 → Milvus Top-K → 组装上下文"""
from config import settings
from rag.embedding import EmbeddingModel
from rag.vector_store import VectorStore

class Retriever:
    def __init__(self):
        self.store = VectorStore()
        self.embedder = EmbeddingModel()

    def retrieve(self, query: str, top_k: int | None = None) -> list[dict]:
        """返回 [{"text", "source", "score"}]，score 越大越相关"""
        vector = self.embedder.encode_query(query)
        return self.store.search(vector, top_k or settings.top_k)

    @staticmethod
    def build_context(hits: list[dict]) -> str:
        """把检索结果拼成给 LLM 的上下文，带来源标注"""
        blocks = []
        for i, hit in enumerate(hits, start=1):
            blocks.append(
                f"[资料{i}] 来源：{hit['source']}（相关度 {hit['score']:.3f}）\n{hit['text']}"
            )
        return "\n\n".join(blocks)

# 检索效果验证：右键 Run 这个文件
if __name__ == "__main__":
    retriever = Retriever()

    queries = [
        "七天无理由退货从哪天开始算",
        "退货运费谁承担",
        "退款多久到账",
        "可以修改收货地址吗",
        "商品拆封了还能退吗",
        "大件家电退货要扣钱吗",
    ]

    for q in queries:
        print("\n" + "=" * 70)
        print(f"问题：{q}")
        print("=" * 70)

        hits = retriever.retrieve(q, top_k=3)
        for i, hit in enumerate(hits, start=1):
            preview = hit["text"].replace("\n", " ")[:100]
            print(f"\n  [{i}] score={hit['score']:.4f}  source={hit['source']}")
            print(f"      {preview}...")