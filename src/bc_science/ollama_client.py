from __future__ import annotations

import json
from collections.abc import Callable, Iterable

import httpx

ProgressCallback = Callable[[dict], None]


class OllamaError(RuntimeError):
    pass


class OllamaClient:
    def __init__(self, base_url: str = "http://127.0.0.1:11434") -> None:
        self.base_url = base_url.rstrip("/")

    def available(self) -> bool:
        try:
            with httpx.Client(timeout=2.0) as client:
                response = client.get(f"{self.base_url}/api/tags")
                return response.is_success
        except httpx.HTTPError:
            return False

    def models(self) -> set[str]:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(f"{self.base_url}/api/tags")
            response.raise_for_status()
            return {item["name"] for item in response.json().get("models", [])}

    def ensure_model(
        self,
        model: str,
        progress_callback: ProgressCallback | None = None,
    ) -> bool:
        installed = self.models()
        if model in installed or f"{model}:latest" in installed:
            return False

        try:
            with httpx.Client(timeout=None) as client:
                with client.stream(
                    "POST",
                    f"{self.base_url}/api/pull",
                    json={"model": model, "stream": True},
                ) as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if not line:
                            continue
                        payload = json.loads(line)
                        if error := payload.get("error"):
                            raise OllamaError(str(error))
                        if progress_callback is not None:
                            progress_callback(payload)
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise OllamaError(f"Errore durante il download di {model}: {exc}") from exc

        return True

    def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        num_ctx: int = 8192,
        num_predict: int = 1800,
        keep_alive: str = "20m",
    ) -> str:
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "think": False,
            "keep_alive": keep_alive,
            "options": {
                "temperature": 0.1,
                "top_p": 0.9,
                "num_ctx": num_ctx,
                "num_predict": num_predict,
            },
        }
        try:
            with httpx.Client(timeout=None) as client:
                response = client.post(f"{self.base_url}/api/chat", json=payload)
                response.raise_for_status()
                return response.json()["message"]["content"].strip()
        except (httpx.HTTPError, KeyError) as exc:
            raise OllamaError(f"Errore Ollama durante la generazione: {exc}") from exc

    def embed(self, model: str, texts: Iterable[str]) -> list[list[float]]:
        items = list(texts)
        if not items:
            return []
        try:
            with httpx.Client(timeout=None) as client:
                response = client.post(
                    f"{self.base_url}/api/embed",
                    json={"model": model, "input": items, "truncate": True},
                )
                response.raise_for_status()
                return response.json()["embeddings"]
        except (httpx.HTTPError, KeyError) as exc:
            raise OllamaError(f"Errore Ollama durante l'embedding: {exc}") from exc
