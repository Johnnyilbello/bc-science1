from bc_science.ollama_client import ChatResult


def test_chat_result_rates_and_durations():
    result = ChatResult(
        content="ok",
        total_duration=5_000_000_000,
        load_duration=500_000_000,
        prompt_eval_duration=1_000_000_000,
        eval_count=100,
        eval_duration=2_000_000_000,
    )
    assert result.total_seconds == 5.0
    assert result.load_seconds == 0.5
    assert result.prompt_seconds == 1.0
    assert result.tokens_per_second == 50.0


def test_chat_result_zero_rate_without_eval_duration():
    result = ChatResult(content="ok", eval_count=100, eval_duration=0)
    assert result.tokens_per_second == 0.0
