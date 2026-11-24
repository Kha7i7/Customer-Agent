"""RAG 检索效果评测：Recall@1 / Recall@3 / MRR

用法：
    python -m scripts.eval_rag
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag.retriever import Retriever

# (问题, 期望命中的来源关键字)
CASES = [
    ("下单后多久发货？", "Q1"),
    ("可以修改收货地址吗？", "Q2"),
    ("物流信息长时间不更新怎么办？", "Q9"),
    ("快递丢件了怎么办？", "Q10"),
    ("显示已签收但是我没收到货", "Q11"),
    ("怎么申请退货？", "Q14"),
    ("退货运费谁承担？", "Q15"),
    ("超过 7 天还能退吗？", "Q17"),
    ("商品拆封了还能退吗？", "Q18"),
    ("换货多久能收到新商品？", "Q20"),
    ("退款多久到账？", "Q24"),
    ("退款包含运费吗？", "Q27"),
    ("可以开发票吗？", "Q6"),
    ("优惠券可以叠加使用吗？", "Q29"),
    ("客服工作时间是什么时候？", "Q37"),
    ("七天无理由从哪天开始算？", "2.1 适用条件"),
    ("大件家电退货要扣钱吗？", "7.2 大件家电"),
    # 以下为改写问法，避免问题与 FAQ 标题完全一致导致指标虚高
    ("耳机有杂音算质量问题吗？", "Q23"),
    ("退的钱退到哪里去？", "Q25"),
    ("刚买完就降价了能补差价吗？", "Q39"),
    ("会员自动续费怎么关闭？", "Q33"),
    ("生鲜坏了能退吗？", "7.1 生鲜商品"),
    ("跨境商品可以退吗？", "7.4 跨境商品"),
]

TOP_K = 3


def main():
    retriever = Retriever()
    hit1 = hit3 = 0
    reciprocal_rank = 0.0

    print(f"{'问题':<24} {'Top1 来源':<38} {'@1':<4} {'@3':<4} 排名")
    print("-" * 96)

    for question, expected in CASES:
        hits = retriever.retrieve(question, top_k=TOP_K)
        sources = [hit["source"] for hit in hits]

        rank = 0
        for i, source in enumerate(sources, start=1):
            if expected in source:
                rank = i
                break

        if rank == 1:
            hit1 += 1
        if rank > 0:
            hit3 += 1
            reciprocal_rank += 1 / rank

        top1 = sources[0] if sources else "-"
        print(
            f"{question:<24} {top1:<38} "
            f"{'✓' if rank == 1 else '✗':<4} {'✓' if rank else '✗':<4} "
            f"{rank or '-'}"
        )

    total = len(CASES)
    print("-" * 96)
    print(f"样本数：{total}，Top-K：{TOP_K}")
    print(f"Recall@1：{hit1 / total:.2%}（{hit1}/{total}）")
    print(f"Recall@3：{hit3 / total:.2%}（{hit3}/{total}）")
    print(f"MRR：{reciprocal_rank / total:.4f}")


if __name__ == "__main__":
    main()
