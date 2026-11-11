"""JSON-constrained Ollama replies are opt-in and do not alter normal chat."""
from __future__ import annotations

from bc_science import ollama_client


class _Response:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"message": {"content": '{"label":"UNVERIFIED"}'}}


class _Client:
    def __init__(self, captured: list[dict]):
        self.captured = captured

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def post(self, _url: str, *, json: dict) -> _Response:
        self.captured.append(json)
        return _Response()


def test_json_mode_only_when_requested(monkeypatch):
    requests: list[dict] = []
    monkeypatch.setattr(
        ollama_client.httpx, "Client",
        lambda **_kwargs: _Client(requests),
    )
    client = ollama_client.OllamaClient("http://127.0.0.1:11434")
    messages = [{"role": "user", "content": "Rispondi in JSON"}]
    assert client.chat("qwen3.5:4b", messages, format_json=True).startswith("{")
    assert client.chat("qwen3.5:4b", messages).startswith("{")
    assert requests[0]["format"] == "json"
    assert "format" not in requests[1]
    assert requests[0]["stream"] is False
    assert requests[1]["stream"] is False
