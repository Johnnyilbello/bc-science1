from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass

import httpx

ProgressCallback = Callable[[dict], None]
TokenCallback = Callable[[str], None]


@dataclass(slots=True)
class ChatResult:
    content: str
    done_reason: str | None = None
    total_duration: int = 0
    load_duration: int = 0
    prompt_eval_count: int = 0
    prompt_eval_cached_count: int = 0
    prompt_eval_duration: int = 0
    eval_count: int = 0
    eval_duration: int = 0

    @property
    def tokens_per_second(self) -> float:
        if self.eval_count <= 0 or self.eval_duration <= 0:
            return 0.0
        return self.eval_count / (self.eval_duration / 1_000_000_000)

    @property
    def total_seconds(self) -> float:
        return self.total_duration / 1_000_000_000 if self.total_duration else 0.0

    @property
    def load_seconds(self) -> float:
        return self.load_duration / 1_000_000_000 if self.load_duration else 0.0

    @property
    def prompt_seconds(self) -> float:
        return self.prompt_eval_duration / 1_000_000_000 if self.prompt_eval_duration else 0.0


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
            with httpx.Client(timeout=None) as client, client.stream(
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

    @staticmethod
    def _chat_payload(
        model: str,
        messages: list[dict[str, str]],
        *,
        stream: bool,
        num_ctx: int,
        num_predict: int,
        keep_alive: str,
    ) -> dict:
        return {
            "model": model,
            "messages": messages,
            "stream": stream,
            "think": False,
            "keep_alive": keep_alive,
            "options": {
                "temperature": 0.1,
                "top_p": 0.9,
                "num_ctx": num_ctx,
                "num_predict": num_predict,
            },
        }

    def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        num_ctx: int = 8192,
        num_predict: int = 1800,
        keep_alive: str = "20m",
    ) -> str:
        payload = self._chat_payload(
            model,
            messages,
            stream=False,
            num_ctx=num_ctx,
            num_predict=num_predict,
            keep_alive=keep_alive,
        )
        try:
            with httpx.Client(timeout=None) as client:
                response = client.post(f"{self.base_url}/api/chat", json=payload)
                response.raise_for_status()
                return response.json()["message"]["content"].strip()
        except (httpx.HTTPError, KeyError) as exc:
            raise OllamaError(f"Errore Ollama durante la generazione: {exc}") from exc

    def chat_stream(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        on_token: TokenCallback | None = None,
        num_ctx: int = 8192,
        num_predict: int = 900,
        keep_alive: str = "20m",
    ) -> ChatResult:
        payload = self._chat_payload(
            model,
            messages,
            stream=True,
            num_ctx=num_ctx,
            num_predict=num_predict,
            keep_alive=keep_alive,
        )
        pieces: list[str] = []
        final: dict = {}

        try:
            with httpx.Client(timeout=None) as client, client.stream(
                "POST",
                f"{self.base_url}/api/chat",
                json=payload,
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line:
                        continue
                    event = json.loads(line)
                    if error := event.get("error"):
                        raise OllamaError(str(error))

                    piece = event.get("message", {}).get("content", "")
                    if piece:
                        pieces.append(piece)
                        if on_token is not None:
                            on_token(piece)

                    if event.get("done"):
                        final = event
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise OllamaError(f"Errore Ollama durante la generazione: {exc}") from exc

        return ChatResult(
            content="".join(pieces).strip(),
            done_reason=final.get("done_reason"),
            total_duration=int(final.get("total_duration", 0) or 0),
            load_duration=int(final.get("load_duration", 0) or 0),
            prompt_eval_count=int(final.get("prompt_eval_count", 0) or 0),
            prompt_eval_cached_count=int(final.get("prompt_eval_cached_count", 0) or 0),
            prompt_eval_duration=int(final.get("prompt_eval_duration", 0) or 0),
            eval_count=int(final.get("eval_count", 0) or 0),
            eval_duration=int(final.get("eval_duration", 0) or 0),
        )

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
