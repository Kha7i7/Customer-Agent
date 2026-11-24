"""知识库灌库：按 Markdown 标题切块 → 向量化 → 写入 Milvus

用法：
    python -m rag.ingest              # 已有数据则跳过
    python -m rag.ingest --force      # 清空重建（改维度后必须用这个）
"""
import argparse
import json
import re
from pathlib import Path

from config import settings
from rag.embedding import EmbeddingModel
from rag.vector_store import VectorStore

SUPPORTED = {".md", ".txt", ".jsonl"}
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
FAQ_RE = re.compile(r"^Q(\d+)\s*[：:]\s*(.+)$")

# ---------- 按标题切分 Markdown ----------
def split_markdown(text: str, max_level: int = 3) -> list[tuple[str, str]]:
    """返回 [(标题路径, 正文)]，标题层级不超过 max_level 才切

    例：("二、七天无理由退货 > 2.1 适用条件", "同时满足以下条件……")
    """
    stack: dict[int, str] = {}
    buf: list[str] = []
    sections: list[tuple[str, str]] = []

    def flush():
        if not buf:
            return
        body = "\n".join(buf).strip()
        buf.clear()
        if body:
            path = " > ".join(stack[lvl] for lvl in sorted(stack))
            sections.append((path, body))

    for line in text.splitlines():
        m = HEADING_RE.match(line)
        if m and len(m.group(1)) <= max_level:
            flush()
            level = len(m.group(1))
            # 丢弃层级更深的旧标题
            stack = {k: v for k, v in stack.items() if k < level}
            stack[level] = m.group(2).strip()
        else:
            buf.append(line)
    flush()
    return sections

# ---------- 超长段落再切分 ----------
def chunk_text(text: str, size: int, overlap: int) -> list[str]:
    if len(text) <= size:
        return [text]

    chunks, start = [], 0
    while start < len(text):
        end = start + size
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = end - overlap
    return chunks

# ---------- 文件加载 ----------
def load_markdown(path: Path) -> list[dict]:
    """按标题切块；FAQ 的 'Q1：xxx' 标题转成 问题+答案 结构"""
    text = path.read_text(encoding="utf-8", errors="ignore")
    docs = []

    for path_title, body in split_markdown(text, max_level=3):
        last_title = path_title.split(" > ")[-1]
        faq = FAQ_RE.match(last_title)

        if faq:
            # FAQ：问题和答案拼一起，让两边都能被检索到
            content = f"问题：{faq.group(2)}\n答案：{body}"
            source = f"{path.name}#Q{faq.group(1)}"
        else:
            content = f"{path_title}\n{body}" if path_title else body
            source = f"{path.name}#{last_title[:60]}"

        for piece in chunk_text(content, settings.chunk_size, settings.chunk_overlap):
            docs.append({"text": piece, "source": source})

    return docs

def load_jsonl(path: Path) -> list[dict]:
    docs = []
    with open(path, encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                print(f"  [跳过] {path.name} 第 {line_no} 行格式错误")
                continue

            question = str(item.get("question", "")).strip()
            answer = str(item.get("answer", item.get("content", ""))).strip()
            if not answer:
                continue

            text = f"问题：{question}\n答案：{answer}" if question else answer
            docs.append({"text": text, "source": f"{path.name}#{item.get('id', line_no)}"})
    return docs

def collect_docs() -> list[dict]:
    kb_dir = settings.kb_dir
    if not kb_dir.exists():
        raise FileNotFoundError(f"知识库目录不存在：{kb_dir}")

    docs: list[dict] = []
    for path in sorted(kb_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED:
            continue

        loaded = load_jsonl(path) if path.suffix.lower() == ".jsonl" else load_markdown(path)
        print(f"  {path.name}: {len(loaded)} 块")
        docs.extend(loaded)

    # 完全重复的文本只留一条
    seen, unique = set(), []
    for doc in docs:
        if doc["text"] in seen:
            continue
        seen.add(doc["text"])
        unique.append(doc)

    print(f"合计 {len(docs)} 块，去重后 {len(unique)} 块")
    return unique

# ---------- 主流程 ----------
def build(force: bool = False):
    store = VectorStore()

    if force:
        print("--force：删除旧集合并重建")
        store.drop()
        store = VectorStore()
    elif store.has_data():
        print(f"集合已有 {store.count()} 条，跳过。重建请加 --force")
        return

    print("\n[1/3] 加载并切分文档")
    docs = collect_docs()
    if not docs:
        print("没有可入库内容")
        return

    print(f"\n[2/3] 向量化（模型 {settings.embedding_model}，来源 {settings.embedding_provider}）")
    embedder = EmbeddingModel()
    vectors = embedder.encode_documents([d["text"] for d in docs])

    dim = len(vectors[0])
    print(f"向量维度 {dim}，共 {len(vectors)} 条")
    if dim != settings.embedding_dim:
        raise ValueError(
            f"维度不匹配：模型输出 {dim}，配置为 {settings.embedding_dim}，请修改 EMBEDDING_DIM 后 --force"
        )

    print("\n[3/3] 写入 Milvus")
    batch = settings.embedding_batch_size
    total = 0
    for start in range(0, len(docs), batch):
        records = [
            {
                "id": start + i,
                "vector": vectors[start + i],
                "text": docs[start + i]["text"],
                "source": docs[start + i]["source"],
            }
            for i in range(len(docs[start:start + batch]))
        ]
        total += store.insert(records)
        print(f"  {total}/{len(docs)}")

    print(f"\n完成：{settings.collection_name} 现有 {store.count()} 条")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    build(force=args.force)