from bc_science.knowledge import classify_domain, system_prompt


def test_fisiologia_classification():
    _, label = classify_domain(
        "Il sarcomero usa actina e miosina, ATP e calcio durante la contrazione."
    )
    assert "Fisiologia" in label


def test_system_prompt_requires_source_fidelity():
    prompt = system_prompt("Fisiologia umana e dello sport")
    assert "fonte primaria" in prompt
    assert "italiano semplice" in prompt
