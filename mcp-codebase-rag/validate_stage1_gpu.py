#!/usr/bin/env python3
"""In-process GPU validation of the Stage-1 precision lift (no HTTP server).

The HTTP path (serve-embed.sh/serve-rerank.sh → rag_core httpx) is already proven
separately (real 1024-d vectors + correct rerank ordering over curl; deterministic
smoke for the wiring). This script proves the remaining claim — "precision
measurably improves with dense/rerank ON" — using the REAL Qwen3 models loaded
straight onto the GPU, by monkeypatching rag_core.embed_texts / rag_core.rerank
to call them directly. Fast (seconds) and avoids long-running background servers.

Run:  serving/.venv has the models; run with the RAG venv but import torch/ST from
      the serving venv path, OR run with the serving venv (it has httpx? no) — so
      we run under the SERVING venv and add the rag dir to sys.path for rag_core.
"""
from __future__ import annotations

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

EMBED_ID = os.environ.get("EMBED_SERVE_MODEL", "Qwen/Qwen3-Embedding-0.6B")
RERANK_ID = os.environ.get("RERANK_SERVE_MODEL", "Qwen/Qwen3-Reranker-0.6B")
DEVICE = os.environ.get("SERVE_DEVICE", "cuda")

PROBES = [
    ("re-order candidate documents with the cross encoder before returning", "rerank"),
    ("combine several ranked result lists into one fused ranking", "_rrf"),
    ("split a source file into function and class chunks", "chunk_file"),
    ("call the http endpoint to vectorize text into embeddings", "embed_texts"),
    ("build the safe full text search match expression from a query", "_fts_query"),
    ("store a vector for each chunk in the sqlite vector table", "index_repo"),
]


def _load_models():
    import torch
    from sentence_transformers import SentenceTransformer
    from transformers import AutoModelForCausalLM, AutoTokenizer

    print(f"loading {EMBED_ID} + {RERANK_ID} on {DEVICE} (fp16) …", flush=True)
    dt = torch.float16 if DEVICE == "cuda" else torch.float32
    embed = SentenceTransformer(EMBED_ID, device=DEVICE, model_kwargs={"torch_dtype": dt})
    embed.max_seq_length = min(getattr(embed, "max_seq_length", 512) or 512, 512)
    tok = AutoTokenizer.from_pretrained(RERANK_ID, padding_side="left")
    rr = AutoModelForCausalLM.from_pretrained(RERANK_ID, torch_dtype=dt).to(DEVICE).eval()
    yes_id = tok.convert_tokens_to_ids("yes")
    no_id = tok.convert_tokens_to_ids("no")
    prefix = (
        "<|im_start|>system\nJudge whether the Document meets the requirements based on "
        'the Query and the Instruct provided. Note that the answer can only be "yes" or '
        '"no".<|im_end|>\n<|im_start|>user\n'
    )
    suffix = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
    pre_ids = tok.encode(prefix, add_special_tokens=False)
    suf_ids = tok.encode(suffix, add_special_tokens=False)

    def gpu_embed(texts):
        v = embed.encode(list(texts), normalize_embeddings=True, convert_to_numpy=True,
                         batch_size=8)
        return [row.tolist() for row in v]

    def gpu_rerank(query, documents):
        instruct = "Given a web search query, retrieve relevant passages that answer the query"
        scores = []
        with torch.no_grad():
            for doc in documents:
                p = f"<Instruct>: {instruct}\n<Query>: {query}\n<Document>: {doc[:1500]}"
                ids = pre_ids + tok.encode(p, add_special_tokens=False) + suf_ids
                inp = torch.tensor([ids[:8192]], device=DEVICE)
                logits = rr(inp).logits[0, -1]
                prob = torch.softmax(torch.stack([logits[no_id], logits[yes_id]]), 0)[1].item()
                scores.append(prob)
        return sorted(range(len(scores)), key=lambda i: scores[i], reverse=True) or None

    return gpu_embed, gpu_rerank


def _rank_of(results, expected):
    for i, r in enumerate(results):
        if r["symbol"] == expected:
            return i + 1
    return None


def _run(rag_core, label, embed_on, rerank_on):
    rag_core.EMBED_BASE_URL = "inproc" if embed_on else ""
    rag_core.RERANK_BASE_URL = "inproc" if rerank_on else ""
    print(f"\n── {label} (embed={'on' if embed_on else 'off'}, rerank={'on' if rerank_on else 'off'}) ──")
    rr = 0.0
    mode = ""
    for q, exp in PROBES:
        res = rag_core.search_code(q, k=8)
        mode = res["mode"]
        rank = _rank_of(res["results"], exp)
        rr += (1.0 / rank) if rank else 0.0
        print(f"  [{('#'+str(rank)) if rank else 'MISS':>4}] want {exp:<12} top5={[r['symbol'] for r in res['results'][:5]]}")
    mrr = rr / len(PROBES)
    print(f"  → mode={mode}  MRR={mrr:.3f}")
    return mrr


def main():
    repo = sys.argv[1] if len(sys.argv) > 1 else HERE
    os.environ["RAG_INDEX_PATH"] = os.path.join(tempfile.mkdtemp(prefix="rag-gpu-"), "i.db")
    os.environ["EMBED_BASE_URL"] = "inproc"  # so index_repo embeds
    import rag_core

    gpu_embed, gpu_rerank = _load_models()
    rag_core.embed_texts = gpu_embed       # type: ignore[assignment]
    rag_core.rerank = gpu_rerank           # type: ignore[assignment]

    print(f"indexing {repo} on GPU …", flush=True)
    info = rag_core.index_repo(repo)
    print(f"  {info['chunks_indexed']} chunks; dense_embedded={info['dense_embedded']}; "
          f"graph={info.get('graph_available')}")

    res = {}
    res["A bm25+graph"] = _run(rag_core, "A bm25+graph", False, False)
    res["B hybrid"] = _run(rag_core, "B hybrid", True, False)
    res["C hybrid+rerank"] = _run(rag_core, "C hybrid+rerank", True, True)

    print("\n════════ SUMMARY (MRR, higher = better) ════════")
    for k, v in res.items():
        print(f"  {k:<20} {v:.3f}")
    floor = res["A bm25+graph"]
    best = max(res, key=res.get)
    print(f"\n  floor (bm25+graph): {floor:.3f}  |  best: {best} {res[best]:.3f}  |  lift: {res[best]-floor:+.3f}")
    print("  ✓ precision improved with dense/rerank ON" if res[best] > floor
          else "  • no lift on these probes (honest signal)")


if __name__ == "__main__":
    main()
