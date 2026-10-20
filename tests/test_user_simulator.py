from types import SimpleNamespace

from eval_tool.models import SimulatorDecision
from eval_tool.user_simulator import UserSimulator, _raw_completion_content


def test_raw_completion_content_reads_assistant_content():
    completion = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content='{"message":"30 phút"}')
            )
        ]
    )
    decision = SimpleNamespace(_raw_response=completion)

    assert _raw_completion_content(decision) == '{"message":"30 phút"}'


def test_raw_completion_content_handles_missing_response():
    assert _raw_completion_content(SimpleNamespace()) == "<unavailable>"


def test_log_decision_persists_raw_and_parsed_response(tmp_path):
    prompt_file = tmp_path / "prompts" / "simulate_user.md"
    prompt_file.parent.mkdir()
    prompt_file.write_text("prompt", encoding="utf-8")
    simulator = UserSimulator(SimpleNamespace(), prompt_file)
    decision = SimulatorDecision(message="30 phút", should_stop=False)

    simulator._log_decision(
        case_id="late-test-001",
        force_continue=True,
        decision=decision,
    )

    record = simulator.log_file.read_text(encoding="utf-8").strip()
    assert '"case_id": "late-test-001"' in record
    assert '"raw_response": "<unavailable>"' in record
    assert '"message": "30 phút"' in record
