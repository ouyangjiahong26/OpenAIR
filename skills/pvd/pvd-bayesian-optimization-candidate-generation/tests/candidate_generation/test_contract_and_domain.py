from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest
from jsonschema import ValidationError

from helpers import (
    ROOT,
    actual_execution,
    experiment,
    measurement,
    request_payload,
    state_facts,
    system_context,
    validate_public_input,
)
from pvd_bo.domain import evaluate_process_context
from pvd_bo.profile import load_model_profile
from pvd_bo.service import run_candidate_generation


def _load_example(name: str) -> dict:
    return json.loads((ROOT / "examples" / name).read_text(encoding="utf-8"))


def test_public_contract_accepts_exact_canonical_state_only() -> None:
    payload = _load_example("current_system_request.json")
    validate_public_input(payload)
    assert set(payload) == {"scientific_state_facts"}


@pytest.mark.parametrize("legacy_key", ["model_profile_id", "process_context", "project_observations"])
def test_public_contract_rejects_legacy_bo_specific_state_views(legacy_key: str) -> None:
    payload = _load_example("current_system_request.json")
    payload[legacy_key] = {} if legacy_key != "model_profile_id" else "legacy"
    with pytest.raises(ValidationError):
        validate_public_input(payload)


def test_first_round_generates_from_base_dataset_only() -> None:
    # R0（尚无同体系真实反馈）基于冻结基线数据集出候选——与独立 GP63 推荐器
    # 的首轮行为一致；BO 节点因此可以在确定性链路中从第一轮就展示候选。
    payload = request_payload(history=[])
    validate_public_input(payload)
    result = run_candidate_generation(payload)
    assert result["candidate_generation_status"] == "generated"
    assert result["training_summary"]["project_count"] == 0
    assert result["training_summary"]["base_count"] == 63
    assert len(result["candidates"]) == 10


def test_domain_mismatch_is_safe_not_applicable() -> None:
    payload = _load_example("mismatch_request.json")
    validate_public_input(payload)
    result = run_candidate_generation(payload)
    assert result["state"] == "succeeded"
    assert result["candidate_generation_status"] == "not_applicable"
    assert result["applicability"]["status"] == "ineligible"
    assert result["candidates"] == []
    assert result["run_diagnostics"] is None
    assert "process_context_mismatch:substrate" in result["applicability"]["issues"]


def test_missing_domain_field_is_indeterminate() -> None:
    payload = request_payload(current_context=system_context(target_angle=None))
    validate_public_input(payload)
    result = run_candidate_generation(payload)
    assert result["candidate_generation_status"] == "not_applicable"
    assert result["applicability"]["status"] == "indeterminate"
    assert "missing_process_context:target_angle" in result["applicability"]["issues"]


def test_cross_system_history_is_excluded_not_silently_mixed() -> None:
    current = experiment(experiment_ref="EXP-CURRENT", context_ref="SYS-A")
    other = experiment(
        experiment_ref="EXP-OTHER",
        context_ref="SYS-B",
        actual=actual_execution(execution_ref="EXEC-OTHER", source_ref="record:other"),
        measurements=[measurement(measurement_ref="MEAS-OTHER", source_ref="record:other")],
    )
    payload = request_payload(
        history=[other, current],
        extra_contexts=[{
            "experimental_system_context_ref": "SYS-B",
            "experimental_system_context": system_context(substrate="Si"),
        }],
    )
    validate_public_input(payload)
    result = run_candidate_generation(payload)
    assert result["candidate_generation_status"] == "generated"
    assert result["training_summary"]["project_count"] == 1


def test_unresolved_history_context_ref_fails_closed() -> None:
    payload = request_payload(history=[experiment(context_ref="SYS-MISSING")])
    # Referential integrity is a deterministic semantic invariant beyond JSON shape.
    with pytest.raises(ValueError, match="does not resolve"):
        run_candidate_generation(payload)


def test_recommendation_and_plan_never_become_real_bo_observations() -> None:
    src = {"origin": "system", "source_ref": "artifact:node9:r1"}
    recommendation = {
        "parameters": [
            {"parameter_key": "power", "display_name": "功率", "recommended_value": 200, "unit": "W"},
            {"parameter_key": "n2_flow", "display_name": "N2 流量", "recommended_value": 17, "unit": "sccm"},
            {"parameter_key": "ar_flow", "display_name": "Ar 流量", "recommended_value": 39, "unit": "sccm"},
            {"parameter_key": "deposition_time", "display_name": "沉积时间", "recommended_value": 2300, "unit": "s"},
        ],
        "validation_goal": "提高 d33",
        "failure_signal": "d33 连续两轮下降",
        "next_step": "调整 N2 比例后复测",
        "provenance": src,
    }
    planned = {
        "parameters": {},
        "other_process_conditions": [],
        "provenance": {"origin": "user", "source_ref": "plan:r1"},
    }
    row = experiment(
        recommendation=recommendation,
        planned=planned,
        actual=None,
        measurements=[],
    )
    row["actual_execution"] = None
    payload = {"scientific_state_facts": state_facts(history=[row])}
    validate_public_input(payload)
    result = run_candidate_generation(payload)
    assert result["candidate_generation_status"] == "generated"
    # 推荐值与计划执行都不进入训练观测。
    assert result["training_summary"]["project_count"] == 0


@pytest.mark.parametrize(
    "actual_status,actual_validity,measurement_status,measurement_validity",
    [
        ("partially_executed", "partial", "final", "valid"),
        ("executed", "invalid", "final", "valid"),
        ("executed", "valid", "provisional", "valid"),
        ("executed", "valid", "final", "invalid"),
    ],
)
def test_non_final_or_invalid_real_facts_are_not_promoted_to_bo_training(
    actual_status: str,
    actual_validity: str,
    measurement_status: str,
    measurement_validity: str,
) -> None:
    row = experiment(
        actual=actual_execution(status=actual_status, validity=actual_validity),
        measurements=[measurement(status=measurement_status, validity=measurement_validity)],
    )
    payload = request_payload(history=[row])
    validate_public_input(payload)
    result = run_candidate_generation(payload)
    assert result["candidate_generation_status"] == "generated"
    # 非真实/非最终/无效事实一律不进入训练观测。
    assert result["training_summary"]["project_count"] == 0


def test_domain_gate_normalizes_real_spellings_but_keeps_real_mismatches() -> None:
    # 真实输入对同一体系有多种表述（材料成分顺序/富化衬底/数值角度）。
    profile, _ = load_model_profile(ROOT, "al40sc_mo_110mm_v2")
    tolerant = evaluate_process_context(
        {
            "substrate": "Mo/Si(100)",
            "target_angle": 30,
            "power_supply_temperature": 200,
            "target_material": "Al-40%Sc",
            "target_substrate_distance_mm": 110,
        },
        profile,
    )
    assert tolerant.status == "eligible"
    different_substrate = evaluate_process_context(
        {
            "substrate": "Si",
            "target_angle": "内偏30度",
            "power_supply_temperature": 200.0,
            "target_material": "40%AlSc",
            "target_substrate_distance_mm": 110.0,
        },
        profile,
    )
    assert different_substrate.status == "ineligible"
    different_orientation = evaluate_process_context(
        {
            "substrate": "Mo",
            "target_angle": "外偏30度",
            "power_supply_temperature": 200.0,
            "target_material": "40%AlSc",
            "target_substrate_distance_mm": 110.0,
        },
        profile,
    )
    assert different_orientation.status == "ineligible"
