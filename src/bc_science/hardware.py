from __future__ import annotations

import platform
import shutil
import subprocess
from dataclasses import dataclass

import psutil


@dataclass(frozen=True, slots=True)
class HardwareInfo:
    ram_gb: float
    cpu_threads: int
    gpu_name: str | None = None
    vram_gb: float | None = None
    os_name: str = platform.system()

    @property
    def summary(self) -> str:
        gpu = self.gpu_name or "GPU non rilevata"
        vram = f", {self.vram_gb:.1f} GB VRAM" if self.vram_gb else ""
        return f"{self.ram_gb:.1f} GB RAM, {self.cpu_threads} thread, {gpu}{vram}"


@dataclass(frozen=True, slots=True)
class ModelPlan:
    turbo: str
    standard: str
    quality: str
    recommended_profile: str
    warning: str | None = None


def _nvidia_info() -> tuple[str | None, float | None]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None, None
    try:
        proc = subprocess.run(
            [
                exe,
                "--query-gpu=name,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
        first = proc.stdout.strip().splitlines()[0]
        name, mem = [part.strip() for part in first.rsplit(",", 1)]
        return name, float(mem) / 1024
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None, None


def detect_hardware() -> HardwareInfo:
    gpu_name, vram_gb = _nvidia_info()
    return HardwareInfo(
        ram_gb=psutil.virtual_memory().total / (1024**3),
        cpu_threads=psutil.cpu_count(logical=True) or 1,
        gpu_name=gpu_name,
        vram_gb=vram_gb,
    )


def select_model_plan(hw: HardwareInfo) -> ModelPlan:
    effective_fast_memory = max(hw.ram_gb, (hw.vram_gb or 0) * 1.7)

    if hw.ram_gb < 6:
        return ModelPlan(
            turbo="qwen3.5:0.8b",
            standard="qwen3.5:0.8b",
            quality="qwen3.5:2b",
            recommended_profile="turbo",
            warning="Meno di 6 GB di RAM: uso il modello 0.8B per evitare rallentamenti.",
        )

    if effective_fast_memory < 11:
        return ModelPlan(
            turbo="qwen3.5:2b",
            standard="qwen3.5:2b",
            quality="qwen3.5:4b",
            recommended_profile="standard",
        )

    if effective_fast_memory < 23:
        return ModelPlan(
            turbo="qwen3.5:2b",
            standard="qwen3.5:4b",
            quality="qwen3.5:4b",
            recommended_profile="standard",
        )

    return ModelPlan(
        turbo="qwen3.5:2b",
        standard="qwen3.5:4b",
        quality="qwen3.5:9b",
        recommended_profile="standard",
    )
