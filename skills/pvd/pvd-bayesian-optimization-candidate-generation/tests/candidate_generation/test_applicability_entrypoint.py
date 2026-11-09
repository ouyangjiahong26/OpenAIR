from __future__ import annotations

from copy import deepcopy
import builtins
import json
from pathlib import Path

import pytest

from helpers import experiment, state_facts, system_context
from pvd_bo import applicability as applicability_module
from pvd_bo.applicability import (
    EXECUTION_INSTANCE_FACT_REF,
    RESEARCH_TASK_FACT_REF,
    SCIENTIFIC_STATE_FACT_REF,
    evaluate_capability_applicability,
)


CAPABILITY_REF = "skill:pvd_bayesian_optimization_candidate_generation:1"
EVALUATOR_REF = "python:pvd_bo.applicability:evaluate_capability_applicability"
FACT_REFS = sorted(
    [RESEARCH_TASK_FACT_REF, EXECUTION_INSTANCE_FACT_REF, SCIENTIFIC_STATE_FACT_REF]
)


def _request(*, facts=None, reentry=True) -> dict:
    instance_id = "instance:generic-repeatable:2"
    state_source = "artifact:validated-feedback"
    return {
        "capabilityRef": CAPABILITY_REF,
        "evaluatorRef": EVALUATOR_REF,
        "runtimeFacts": {
            RESEARCH_TASK_FACT_REF: {
                "taskId": "task-1",
                "reentryExecutionInstances": (
                    [{
                        "instance": {"substageInstanceId": instance_id},
                        "triggeringScientificStateVersion": 7,
                        "triggeringStateSourceRefs": [state_source],
                    }]
                    if reentry
                    else []
                ),
            },
            EXECUTION_INSTANCE_FACT_REF: {"substageInstanceId": instance_id},
            SCIENTIFIC_STATE_FACT_REF: {
                "scientificStateVersion": 7,
                "scientificFacts": facts if facts is not None else state_facts(),
                "sourceRefs": [state_source],
            },
        },
    }


def _assert_typed(result: dict, status: str) -> None:
    assert result == {
        "capabilityRef": CAPABILITY_REF,
        "evaluatorRef": EVALUATOR_REF,
        "status": status,
        "evaluatedRuntimeFactRefs": FACT_REFS,
    }


def test_same_system_real_feedback_returns_typed_eligible() -> None:
    _assert_typed(evaluate_capability_applicability(_request()), "eligible")


def test_same_system_fail_returns_typed_ineligible() -> None:
    facts = state_facts(current_context=system_context(substrate="Si"))
    _assert_typed(evaluate_capability_applicability(_request(facts=facts)), "ineligible")


def test_first_round_without_reentry_is_eligible_via_base_dataset() -> None:
    # 新契约：R0（无重入、无真实反馈）同样 eligible——BO 节点在确定性链路中
    # 从第一轮就基于冻结基线数据集出候选。
    _assert_typed(evaluate_capability_applicability(_request(reentry=False)), "eligible")


def test_reentry_without_usable_real_feedback_still_eligible() -> None:
    # 反馈轮没有可用真实反馈时回退基线数据集，不阻断。
    facts = state_facts(history=[])
    _assert_typed(evaluate_capability_applicability(_request(facts=facts)), "eligible")


def test_missing_required_process_context_fails_closed() -> None:
    facts = state_facts(current_context=system_context(target_angle=None))
    _assert_typed(evaluate_capability_applicability(_request(facts=facts)), "evaluation_failed")


def test_cross_system_only_feedback_does_not_block_current_system_bo() -> None:
    # 跨体系反馈不进入训练观测（技能内同体系过滤保证）；当前体系仍 eligible。
    facts = state_facts(
        history=[experiment(experiment_ref="EXP-B", context_ref="SYS-B")],
        extra_contexts=[{
            "experimental_system_context_ref": "SYS-B",
            "experimental_system_context": system_context(substrate="Si"),
        }],
    )
    _assert_typed(evaluate_capability_applicability(_request(facts=facts)), "eligible")


def test_missing_runtime_fact_fails_closed_without_false_provenance() -> None:
    request = _request()
    request["runtimeFacts"].pop(EXECUTION_INSTANCE_FACT_REF)
    result = evaluate_capability_applicability(request)
    assert result["status"] == "evaluation_failed"
    assert result["evaluatedRuntimeFactRefs"] == []


@pytest.mark.parametrize(
    "mutation",
    [
        lambda request: request["runtimeFacts"][SCIENTIFIC_STATE_FACT_REF].pop("scientificFacts"),
        lambda request: request["runtimeFacts"][SCIENTIFIC_STATE_FACT_REF]["scientificFacts"].pop("current_experimental_system_context_ref"),
        lambda request: request["runtimeFacts"][SCIENTIFIC_STATE_FACT_REF].update({"sourceRefs": []}),
        lambda request: request["runtimeFacts"][RESEARCH_TASK_FACT_REF]["reentryExecutionInstances"][0].update({"triggeringScientificStateVersion": 6}),
    ],
)
def test_malformed_authority_fails_closed(mutation) -> None:
    request = _request()
    mutation(request)
    _assert_typed(evaluate_capability_applicability(request), "evaluation_failed")


def test_existing_evaluator_failure_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        applicability_module,
        "evaluate_process_context",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("failure")),
    )
    _assert_typed(evaluate_capability_applicability(_request()), "evaluation_failed")


def test_entrypoint_reuses_existing_process_context_evaluator(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    real = applicability_module.evaluate_process_context

    def recording(process_context, profile):
        calls.append((deepcopy(dict(process_context)), profile["profile_id"]))
        return real(process_context, profile)

    monkeypatch.setattr(applicability_module, "evaluate_process_context", recording)
    _assert_typed(evaluate_capability_applicability(_request()), "eligible")
    assert calls == [(system_context(), "al40sc_mo_110mm_v2")]


def test_entrypoint_does_not_run_candidate_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def reject_candidate_service_import(name, *args, **kwargs):
        if name == "pvd_bo.service" or name.startswith("pvd_bo.service."):
            raise AssertionError("candidate generation must not be imported")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_candidate_service_import)
    _assert_typed(evaluate_capability_applicability(_request()), "eligible")


def test_identity_fields_are_echoed_not_hard_coded() -> None:
    request = _request()
    request["capabilityRef"] = "skill:alternate-definition:3"
    result = evaluate_capability_applicability(request)
    assert result["capabilityRef"] == request["capabilityRef"]
    assert result["status"] == "eligible"


def test_invalid_request_identity_raises_for_generic_producer_to_fail_closed() -> None:
    request = _request()
    request["evaluatorRef"] = ""
    with pytest.raises(ValueError, match="capabilityRef and evaluatorRef"):
        evaluate_capability_applicability(request)


def test_capability_owned_evaluator_descriptor_is_machine_readable() -> None:
    root = Path(__file__).resolve().parents[2]
    descriptor = json.loads((root / "applicability_evaluator.json").read_text(encoding="utf-8"))
    assert descriptor["evaluatorRef"] == EVALUATOR_REF
    assert sorted(descriptor["requiredRuntimeFactRefs"]) == FACT_REFS
    assert descriptor["scientificStateFactPath"] == "scientificFacts"
    assert descriptor["scientificStateFactSchema"] == "applicability_runtime_fact.schema.json"
    assert descriptor["modelProfileId"] == "al40sc_mo_110mm_v2"


def test_applicability_fact_schema_reuses_canonical_pvd_state_facts() -> None:
    root = Path(__file__).resolve().parents[2]
    runtime_schema = json.loads((root / "applicability_runtime_fact.schema.json").read_text(encoding="utf-8"))
    assert runtime_schema["$ref"] == "https://openair.local/schemas/pvd/pvd-scientific-state-facts-v1.schema.json"
