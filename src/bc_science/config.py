from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

APP_NAME = "BCScience"
DEFAULT_EMBED_MODEL = "qwen3-embedding:0.6b"
VALID_PROFILES = {"turbo", "standard", "quality"}


def app_home() -> Path:
    override = os.getenv("BC_SCIENCE_HOME")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        base = Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / APP_NAME
    return Path.home() / ".bc-science"


@dataclass(slots=True)
class AppConfig:
    generation_model: str = "qwen3.5:4b"
    turbo_model: str = "qwen3.5:2b"
    quality_model: str = "qwen3.5:9b"
    embedding_model: str = DEFAULT_EMBED_MODEL
    ollama_url: str = "http://127.0.0.1:11434"
    chunk_chars: int = 5200
    chunk_overlap: int = 450
    context_chunks: int = 6
    num_ctx: int = 8192
    num_predict: int = 1800
    keep_alive: str = "20m"
    profile: str = "standard"

    @property
    def path(self) -> Path:
        return app_home() / "config.json"

    def save(self) -> Path:
        home = app_home()
        home.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        return self.path

    @classmethod
    def load(cls) -> AppConfig:
        path = app_home() / "config.json"
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in raw.items() if k in allowed})

    def model_for_profile(self, profile: str | None = None) -> str:
        selected = (profile or self.profile).lower()
        if selected == "turbo":
            return self.turbo_model
        if selected == "quality":
            return self.quality_model
        return self.generation_model


def resolve_ask_profile(requested: str, *, deep: bool) -> str:
    selected = requested.lower().strip()
    if selected == "auto":
        return "standard" if deep else "turbo"
    if selected not in VALID_PROFILES:
        allowed = ", ".join(["auto", *sorted(VALID_PROFILES)])
        raise ValueError(f"Profilo non valido: {requested}. Usa: {allowed}.")
    return selected
