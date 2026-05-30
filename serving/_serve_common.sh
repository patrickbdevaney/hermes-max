#!/usr/bin/env bash
# Shared launcher for the Stage-1 model servers. SOURCED by serve-embed.sh /
# serve-rerank.sh once they've set: ROLE (embed|rerank), PORT, MODEL (HF id),
# VLLM_TASK (embed|score). Picks a backend:
#   • vLLM   — prod / the your inference host (GPU). Used when SERVE_BACKEND=vllm, or =auto and
#              `vllm` is on PATH. Exposes the same OpenAI-compatible endpoints.
#   • local  — dev box / laptop (no vLLM). serving/local_serve.py, CPU by default
#              (SERVE_DEVICE=cuda to use a GPU). Same wire contract as vLLM.
# Either way the result is an OpenAI/Cohere-shaped endpoint mcp-codebase-rag can
# point EMBED_BASE_URL / RERANK_BASE_URL at. Kill it → RAG degrades gracefully.
SERVE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="${SERVE_BACKEND:-auto}"
HOST="${SERVE_HOST:-127.0.0.1}"

_have_vllm() { command -v vllm >/dev/null 2>&1; }

serve_role() {
  if [ "${BACKEND}" = "vllm" ] || { [ "${BACKEND}" = "auto" ] && _have_vllm; }; then
    echo "→ serving ${ROLE} via vLLM: ${MODEL} on ${HOST}:${PORT} (task=${VLLM_TASK})"
    # shellcheck disable=SC2086
    exec vllm serve "${MODEL}" --task "${VLLM_TASK}" --port "${PORT}" --host "${HOST}" \
         --served-model-name "${MODEL}" ${VLLM_EXTRA:-}
  fi

  local venv="${SERVE_DIR}/.venv" py="${SERVE_DIR}/.venv/bin/python"
  if [ ! -x "${py}" ]; then
    echo "→ creating serving venv (one-time) …"
    python3 -m venv "${venv}"
    "${py}" -m pip install -q --upgrade pip >/dev/null 2>&1 || true
  fi
  local stamp="${venv}/.requirements.sha" req="${SERVE_DIR}/requirements-serving.txt"
  local cur; cur="$(sha1sum "${req}" | awk '{print $1}')"
  if [ "$(cat "${stamp}" 2>/dev/null)" != "${cur}" ]; then
    echo "→ installing serving deps (first run is slow: torch + transformers) …"
    if [ "${SERVE_DEVICE:-cpu}" = "cpu" ]; then
      "${py}" -m pip install -q torch --index-url https://download.pytorch.org/whl/cpu \
        || "${py}" -m pip install -q torch
    fi
    "${py}" -m pip install -q -r "${req}" && echo "${cur}" >"${stamp}"
  fi
  # If the GPU is requested but the installed torch is CPU-only (e.g. a prior CPU
  # run left a +cpu wheel), swap in the CUDA build so SERVE_DEVICE=cuda works.
  if [ "${SERVE_DEVICE:-cpu}" = "cuda" ] && \
     ! "${py}" -c "import torch,sys; sys.exit(0 if torch.cuda.is_available() else 1)" 2>/dev/null; then
    echo "→ SERVE_DEVICE=cuda but torch can't see the GPU — reinstalling the CUDA build …"
    "${py}" -m pip uninstall -y torch >/dev/null 2>&1
    "${py}" -m pip install -q torch
  fi
  echo "→ serving ${ROLE} via local_serve.py: ${MODEL} on ${HOST}:${PORT} (device=${SERVE_DEVICE:-cpu})"
  exec "${py}" "${SERVE_DIR}/local_serve.py" --role "${ROLE}" --model "${MODEL}" \
       --port "${PORT}" --host "${HOST}"
}
