from __future__ import annotations

import json
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval_tool.bootstrap import build_qwen, build_runner, load_cases  # noqa: E402
from eval_tool.case_generator import CaseGenerator  # noqa: E402
from eval_tool.config import load_config  # noqa: E402
from eval_tool.qwen_client import QwenClient  # noqa: E402
from eval_tool.runner import EvaluationRunner  # noqa: E402


st.set_page_config(page_title="TicketAgents Eval", layout="wide")
st.title("TicketAgents Healthcheck & Multi-turn Evaluation")


@st.cache_resource
def get_config():
    return load_config(ROOT)


def case_files() -> list[Path]:
    return sorted((ROOT / "data" / "seeds").glob("*.yaml")) + sorted(
        (ROOT / "data" / "generated").glob("*.yaml")
    )


config = get_config()
tab_health, tab_generate, tab_eval, tab_results = st.tabs(
    ["Healthcheck", "Generate cases", "Run evaluation", "Result detail"]
)

with tab_health:
    st.subheader("Healthcheck")
    selected_agents = st.multiselect(
        "Agents",
        options=list(config.agents),
        default=list(config.agents),
        format_func=lambda value: config.agents[value].display_name,
    )
    if st.button("Check selected agents", type="primary"):
        runner = EvaluationRunner(config)
        for agent_id in selected_agents:
            with st.spinner(f"Checking {agent_id}..."):
                try:
                    result = runner.healthcheck(agent_id)
                except Exception as exc:
                    st.error(f"{agent_id}: {exc}")
                    continue
            color = {
                "Healthy": st.success,
                "Slow": st.warning,
                "Error": st.error,
            }[result.status]
            color(
                f"{agent_id}: {result.status} — "
                f"TTFT={result.trace.first_answer_token_ms} ms — "
                f"total={result.trace.total_duration_ms} ms"
            )
            rows = [
                {
                    "order": index + 1,
                    "tool": tool.name,
                    "duration_ms": tool.duration_ms,
                    "status": tool.status,
                    "timing_source": tool.timing_source,
                }
                for index, tool in enumerate(result.trace.tool_calls)
            ]
            if rows:
                st.dataframe(rows, use_container_width=True)
            with st.expander(f"Trace {agent_id}"):
                st.json(result.model_dump(mode="json"))

with tab_generate:
    st.subheader("Generate multi-turn cases")
    agent_id = st.selectbox("Agent", options=list(config.agents), key="gen_agent")
    count = st.number_input("Number of cases", min_value=1, max_value=20, value=3)
    category = st.selectbox(
        "Category",
        ["happy-path", "missing-info", "state", "security", "boundary"],
    )
    seeds = load_cases(ROOT / "data" / "seeds" / f"{agent_id}.yaml")
    seed_ids = st.multiselect(
        "Seed cases",
        [case.id for case in seeds],
        default=[case.id for case in seeds[:2]],
    )
    if st.button("Generate", type="primary"):
        try:
            qwen = QwenClient(build_qwen(config, temperature=0.7))
            generator = CaseGenerator(
                qwen,
                ROOT / "prompts" / "generate_case.md",
                config.generated_dir,
            )
            selected = [case for case in seeds if case.id in seed_ids]
            prompt_path = ROOT / config.get_agent(agent_id).prompt_file
            generated = generator.generate(
                agent_id=agent_id,
                system_prompt=prompt_path.read_text(encoding="utf-8"),
                seed_cases=selected,
                count=int(count),
                category=category,
            )
            st.success(f"Generated {len(generated)} cases")
            st.json([case.model_dump(mode="json") for case in generated])
        except Exception as exc:
            st.exception(exc)

with tab_eval:
    st.subheader("Run multi-turn evaluation")
    files = case_files()
    if not files:
        st.info("No case files found")
    else:
        selected_file = st.selectbox(
            "Case file",
            options=files,
            format_func=lambda path: str(path.relative_to(ROOT)),
        )
        loaded = load_cases(selected_file)
        selected_ids = st.multiselect(
            "Cases",
            options=[case.id for case in loaded],
            default=[case.id for case in loaded],
        )
        if st.button("Run evaluation", type="primary"):
            try:
                runner = build_runner(config)
                selected = [case for case in loaded if case.id in selected_ids]
                progress = st.progress(0)
                rows = []
                for index, case in enumerate(selected, start=1):
                    with st.spinner(f"Running {case.id}..."):
                        result = runner.run_case(case)
                    rows.append(
                        {
                            "case": case.id,
                            "agent": case.agent,
                            "verdict": result.evaluation.verdict,
                            "score": result.evaluation.average_score,
                            "duration_seconds": result.duration_seconds,
                            "file": result.result_file,
                        }
                    )
                    progress.progress(index / len(selected))
                st.dataframe(rows, use_container_width=True)
            except Exception as exc:
                st.exception(exc)

with tab_results:
    st.subheader("Result detail")
    result_files = sorted(config.runs_dir.glob("*.json"), reverse=True)
    if not result_files:
        st.info("No result files yet")
    else:
        selected_result = st.selectbox(
            "Result file",
            result_files,
            format_func=lambda path: path.name,
        )
        content = json.loads(selected_result.read_text(encoding="utf-8"))
        st.download_button(
            "Download JSON",
            data=json.dumps(content, ensure_ascii=False, indent=2),
            file_name=selected_result.name,
            mime="application/json",
        )
        st.json(content)
