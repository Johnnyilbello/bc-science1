from bc_science.hardware import HardwareInfo, select_model_plan


def test_low_memory_prefers_small_model():
    plan = select_model_plan(HardwareInfo(ram_gb=8, cpu_threads=4))
    assert plan.standard == "qwen3.5:2b"


def test_mid_memory_prefers_4b():
    plan = select_model_plan(HardwareInfo(ram_gb=16, cpu_threads=8))
    assert plan.standard == "qwen3.5:4b"


def test_high_memory_unlocks_9b_quality():
    plan = select_model_plan(HardwareInfo(ram_gb=32, cpu_threads=16))
    assert plan.quality == "qwen3.5:9b"


def test_gpu_memory_can_raise_standard_profile():
    plan = select_model_plan(
        HardwareInfo(
            ram_gb=10,
            cpu_threads=8,
            gpu_name="Test GPU",
            vram_gb=8,
        )
    )
    assert plan.standard == "qwen3.5:4b"
