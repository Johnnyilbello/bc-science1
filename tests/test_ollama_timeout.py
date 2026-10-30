import httpx
import pytest
from bc_science.ollama_client import OllamaClient, OllamaError


def test_embedding_timeout_is_bounded_and_explains_recovery(monkeypatch):
    seen = {}
    class StalledClient:
        def __init__(self, **kwargs):
            seen.update(kwargs)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def post(self, *args, **kwargs):
            raise httpx.ReadTimeout('local model busy')
    monkeypatch.setattr('bc_science.ollama_client.httpx.Client', StalledClient)
    with pytest.raises(OllamaError, match='attendi e riprova'):
        OllamaClient().embed('test', ['domanda'])
    assert seen['timeout'].read == 180
    assert seen['timeout'].connect == 10
