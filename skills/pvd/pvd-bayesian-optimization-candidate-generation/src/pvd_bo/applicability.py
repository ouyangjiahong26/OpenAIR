from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .config import configured_model_profile_id
from .domain import evaluate_process_context
from .profile import load_model_profile
from .state_observations import resolve_scientific_state_facts

SCIENTIFIC_STATE_FACT_REF = "runtime:scientific-state:v1"
RESEARCH_TASK_FACT_REF = "runtime:research-task:v1"
EXECUTION_INSTANCE_FACT_REF = "runtime:execution-instance:v1"

ELIGIBLE = "eligible"
INELIGIBLE = "ineligible"
EVALUATION_FAILED = "evaluation_failed"

_REQUIRED_FACT_REFS = (
    RESEARCH_TASK_FACT_REF,
    EXECUTION_INSTANCE_FACT_REF,
    SCIENTIFIC_STATE_FACT_REF,
)


def _typed_result(
    *,
    capability_ref: str,
    evaluator_ref: str,
    status: str,
    evaluated_fact_refs: list[str],
) -> dict[str, Any]:
    return {
        "capabilityRef": capability_ref,
        "evaluatorRef": evaluator_ref,
        "status": status,
        "evaluatedRuntimeFactRefs": evaluated_fact_refs,
    }


def _skill_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _evaluate(
    *,
    capability_ref: str,
    evaluator_ref: str,
    runtime_facts: Mapping[str, Any],
) -> dict[str, Any]:
    if not all(isinstance(runtime_facts.get(ref), Mapping) for ref in _REQUIRED_FACT_REFS):
        return _typed_result(
            capability_ref=capability_ref,
            evaluator_ref=evaluator_ref,
            status=EVALUATION_FAILED,
            evaluated_fact_refs=[],
        )

    state_fact = runtime_facts[SCIENTIFIC_STATE_FACT_REF]
    facts = state_fact.get("scientificFacts")
    source_refs = state_fact.get("sourceRefs")
    if not isinstance(facts, Mapping) or not isinstance(source_refs, list) or not source_refs:
        # All three fact refs were reached; their content failed validation.
        return _typed_result(
            capability_ref=capability_ref,
            evaluator_ref=evaluator_ref,
            status=EVALUATION_FAILED,
            evaluated_fact_refs=sorted(_REQUIRED_FACT_REFS),
        )

    # A declared re-entry authority must name the current scientific state
    # version; a stale trigger can never authorize this evaluation.
    current_version = state_fact.get("scientificStateVersion")
    reentries = runtime_facts[RESEARCH_TASK_FACT_REF].get("reentryExecutionInstances")
    if isinstance(reentries, list) and reentries:
        for row in reentries:
            if not isinstance(row, Mapping) or row.get(
                "triggeringScientificStateVersion"
            ) != current_version:
                return _typed_result(
                    capability_ref=capability_ref,
                    evaluator_ref=evaluator_ref,
                    status=EVALUATION_FAILED,
                    evaluated_fact_refs=sorted(_REQUIRED_FACT_REFS),
                )

    try:
        root = _skill_root()
        profile_id = configured_model_profile_id(root)
        profile, _profile_sha = load_model_profile(root, profile_id)
        _current_ref, current_context, _history = resolve_scientific_state_facts(facts)
        applicability = evaluate_process_context(current_context, profile)
    except Exception:  # noqa: BLE001 - the producer fails closed on any evaluator fault
        return _typed_result(
            capability_ref=capability_ref,
            evaluator_ref=evaluator_ref,
            status=EVALUATION_FAILED,
            evaluated_fact_refs=sorted(_REQUIRED_FACT_REFS),
        )

    # R0（尚无同体系真实反馈）同样 eligible：技能按冻结基线数据集出首轮候选，
    # 跨体系反馈永不进入训练（技能内门禁保证）。仅当前体系与模型档案定义域
    # 不符时 ineligible；上下文缺失（indeterminate）按 fail-closed 处理。
    status = (
        applicability.status
        if applicability.status == ELIGIBLE or applicability.status == INELIGIBLE
        else EVALUATION_FAILED
    )
    return _typed_result(
        capability_ref=capability_ref,
        evaluator_ref=evaluator_ref,
        status=status,
        evaluated_fact_refs=sorted(_REQUIRED_FACT_REFS),
    )


def evaluate_capability_applicability(request: Mapping[str, Any]) -> dict[str, Any]:
    """Typed same-system applicability for the BO capability binding.

    This entrypoint never runs candidate generation and never invents
    provenance: malformed authority, missing runtime facts, stale re-entry
    triggers, and evaluator faults all fail closed as ``evaluation_failed``.
    """
    capability_ref = str(request.get("capabilityRef") or "").strip()
    evaluator_ref = str(request.get("evaluatorRef") or "").strip()
    if not capability_ref or not evaluator_ref:
        raise ValueError("capabilityRef and evaluatorRef are required")
    runtime_facts = request.get("runtimeFacts")
    if not isinstance(runtime_facts, Mapping):
        return _typed_result(
            capability_ref=capability_ref,
            evaluator_ref=evaluator_ref,
            status=EVALUATION_FAILED,
            evaluated_fact_refs=[],
        )
    return _evaluate(
        capability_ref=capability_ref,
        evaluator_ref=evaluator_ref,
        runtime_facts=runtime_facts,
    )


__all__ = [
    "EXECUTION_INSTANCE_FACT_REF",
    "RESEARCH_TASK_FACT_REF",
    "SCIENTIFIC_STATE_FACT_REF",
    "evaluate_capability_applicability",
]
