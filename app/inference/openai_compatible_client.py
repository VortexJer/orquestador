"""Cliente HTTP contra cualquier servidor que exponga la API
OpenAI-compatible de chat completions: vLLM, llama.cpp server, Ollama
(modo compat) o LM Studio. Mismo cliente sirve para:

- Desarrollo local sin GPU: apuntar base_url a un llama.cpp server o
  Ollama corriendo Qwen2.5-Coder-7B en CPU (lento pero funcional para
  validar logica).
- Produccion en la L4: apuntar base_url al vLLM que sirve
  Qwen2.5-Coder-14B-Instruct-AWQ.

El swap es 100% de configuracion (INFERENCE_BASE_URL), no de codigo.
"""
from __future__ import annotations

import httpx

from app.inference.base import InferenceClient


class OpenAICompatibleClient(InferenceClient):
    def __init__(self, base_url: str, api_key: str | None = None, timeout_s: float = 60.0):
        self._base_url = base_url.rstrip("/")
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._http = httpx.Client(base_url=self._base_url, headers=headers, timeout=timeout_s)

    def chat(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
        max_tokens: int = 2000,
    ) -> str:
        response = self._http.post(
            "/chat/completions",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]

    def close(self) -> None:
        self._http.close()
