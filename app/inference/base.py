"""Interfaz comun para clientes de inferencia. Un especialista no sabe
(ni le importa) si esta hablando con el MockClient, con llama.cpp
server corriendo Qwen2.5-Coder-7B en CPU/GPU de desarrollo, o con vLLM
en la L4 de produccion - todos exponen el mismo metodo `chat`.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class InferenceClient(ABC):
    @abstractmethod
    def chat(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
        max_tokens: int = 2000,
    ) -> str:
        """Devuelve el contenido de texto de la respuesta del modelo."""
