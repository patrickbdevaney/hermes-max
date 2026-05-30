#!/usr/bin/env python3
"""Lean, self-hosted OpenAI-compatible serving shim for the Stage-1 models.

This is the DEV-BOX backend (laptop / any machine without a vLLM-capable GPU
setup). On the your inference host, serve-embed.sh / serve-rerank.sh prefer vLLM and never touch
this file. Same wire contract either way, so mcp-codebase-rag can't tell them
apart:

  --role embed   →  POST /v1/embeddings   {model,input:[...]}  (Qwen3-Embedding)
                    -> {"data":[{"embedding":[...]}], "model":...}
  --role rerank  →  POST /rerank and /v1/rerank  {model,query,documents:[...]}
                    -> {"results":[{"index":i,"relevance_score":s}, ...]}

Reranker scoring follows Qwen3-Reranker's official "yes/no" causal-LM template
(probability of "yes"). CPU by default (plenty for 0.6B + a handful of queries);
set SERVE_DEVICE=cuda to use the GPU. Degrades nothing — if a model can't load,
it fails loudly at startup so the launcher's healthcheck reports it and RAG stays
in its BM25+graph fallback.
"""
from __future__ import annotations

import argparse
import os

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

DEVICE = os.environ.get("SERVE_DEVICE", "cpu")

app = FastAPI()
_STATE: dict = {}


# ── embeddings ────────────────────────────────────────────────────────────────
class EmbedReq(BaseModel):
    input: list[str] | str
    model: str | None = None


def _load_embed(model_id: str):
    from sentence_transformers import SentenceTransformer

    _STATE["embed"] = SentenceTransformer(model_id, device=DEVICE)
    _STATE["embed_id"] = model_id


@app.post("/v1/embeddings")
@app.post("/embeddings")
def embeddings(req: EmbedReq):
    texts = [req.input] if isinstance(req.input, str) else list(req.input)
    vecs = _STATE["embed"].encode(texts, normalize_embeddings=True, convert_to_numpy=True)
    data = [{"object": "embedding", "index": i, "embedding": v.tolist()} for i, v in enumerate(vecs)]
    return {"object": "list", "data": data, "model": _STATE.get("embed_id")}


# ── reranker (Qwen3-Reranker official yes/no template) ───────────────────────
class RerankReq(BaseModel):
    query: str
    documents: list[str]
    model: str | None = None
    top_n: int | None = None


_RR_INSTRUCT = "Given a web search query, retrieve relevant passages that answer the query"
_RR_PREFIX = (
    "<|im_start|>system\nJudge whether the Document meets the requirements based on the "
    'Query and the Instruct provided. Note that the answer can only be "yes" or "no".'
    "<|im_end|>\n<|im_start|>user\n"
)
_RR_SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"


def _load_rerank(model_id: str):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(model_id, padding_side="left")
    model = AutoModelForCausalLM.from_pretrained(model_id).to(DEVICE).eval()
    _STATE.update(
        rr_tok=tok,
        rr_model=model,
        rr_id=model_id,
        rr_torch=torch,
        rr_yes=tok.convert_tokens_to_ids("yes"),
        rr_no=tok.convert_tokens_to_ids("no"),
        rr_prefix_ids=tok.encode(_RR_PREFIX, add_special_tokens=False),
        rr_suffix_ids=tok.encode(_RR_SUFFIX, add_special_tokens=False),
    )


def _rr_score(query: str, documents: list[str]) -> list[float]:
    tok = _STATE["rr_tok"]
    model = _STATE["rr_model"]
    torch = _STATE["rr_torch"]
    pairs = [
        f"<Instruct>: {_RR_INSTRUCT}\n<Query>: {query}\n<Document>: {doc}" for doc in documents
    ]
    scores: list[float] = []
    with torch.no_grad():
        for p in pairs:
            ids = _STATE["rr_prefix_ids"] + tok.encode(p, add_special_tokens=False) + _STATE["rr_suffix_ids"]
            ids = ids[:8192]
            inp = torch.tensor([ids], device=DEVICE)
            logits = model(inp).logits[0, -1]
            yes = logits[_STATE["rr_yes"]]
            no = logits[_STATE["rr_no"]]
            prob = torch.softmax(torch.stack([no, yes]), dim=0)[1].item()
            scores.append(float(prob))
    return scores


@app.post("/v1/rerank")
@app.post("/rerank")
def rerank(req: RerankReq):
    scores = _rr_score(req.query, req.documents)
    ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    if req.top_n:
        ranked = ranked[: req.top_n]
    results = [{"index": i, "relevance_score": scores[i]} for i in ranked]
    return {"model": _STATE.get("rr_id"), "results": results}


@app.get("/health")
@app.get("/v1/models")
def health():
    role = _STATE.get("role")
    mid = _STATE.get("embed_id") or _STATE.get("rr_id")
    return {"status": "ok", "role": role, "model": mid, "device": DEVICE,
            "data": [{"id": mid, "object": "model"}]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--role", required=True, choices=["embed", "rerank"])
    ap.add_argument("--model", required=True)
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    _STATE["role"] = args.role
    print(f"[local_serve] loading {args.role} model {args.model} on {DEVICE} …", flush=True)
    if args.role == "embed":
        _load_embed(args.model)
    else:
        _load_rerank(args.model)
    print(f"[local_serve] ready on http://{args.host}:{args.port} (/{args.role})", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
