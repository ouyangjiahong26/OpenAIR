from __future__ import annotations

import math
from typing import Any, Mapping

PUBLIC_PARAMETER_TO_INTERNAL_FEATURE = {
    "power": ("power_w", "W"),
    "n2_flow": ("n2_sccm", "sccm"),
    "ar_flow": ("ar_sccm", "sccm"),
    "deposition_time": ("time_s", "s"),
}


def _finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    return value


def resolve_scientific_state_facts(
    scientific_state_facts: Mapping[str, Any],
) -> tuple[str, Mapping[str, Any], list[Mapping[str, Any]]]:
    """Resolve the canonical current experimental system and validate state identities.

    This is Skill-local PVD state consumption, not a compatibility mapper.  The public
    input stays the exact frozen ScientificState fact shape; this function only resolves
    its stable refs and prepares values needed by the BO implementation.
    """

    facts = _mapping(scientific_state_facts, "scientific_state_facts")
    contexts = facts.get("experimental_system_contexts")
    history = facts.get("experiment_history")
    current_ref = str(facts.get("current_experimental_system_context_ref") or "").strip()
    if not isinstance(contexts, list) or not isinstance(history, list):
        raise ValueError("scientific_state_facts contexts/history must be arrays")
    if not current_ref:
        raise ValueError("current_experimental_system_context_ref is missing")

    context_by_ref: dict[str, Mapping[str, Any]] = {}
    for index, raw in enumerate(contexts):
        row = _mapping(raw, f"experimental_system_contexts[{index}]")
        ref = str(row.get("experimental_system_context_ref") or "").strip()
        context = _mapping(
            row.get("experimental_system_context"),
            f"experimental_system_contexts[{index}].experimental_system_context",
        )
        context_by_ref[ref] = context

    if current_ref not in context_by_ref:
        raise ValueError(
            f"current experimental system context is unresolvable: {current_ref}"
        )

    normalized_history: list[Mapping[str, Any]] = []
    for index, raw in enumerate(history):
        row = _mapping(raw, f"experiment_history[{index}]")
        context_ref = str(row.get("experimental_system_context_ref") or "").strip()
        if context_ref and context_ref not in context_by_ref:
            # Referential integrity is a deterministic semantic invariant beyond
            # JSON shape: a history row pointing at an unknown system is a
            # corrupted fact tree, never silently dropped training data.
            raise ValueError(
                f"experiment history context does not resolve: {context_ref}"
            )
        normalized_history.append(row)

    return current_ref, context_by_ref[current_ref], normalized_history


def _actual_parameters_as_internal_features(
    actual_execution: Mapping[str, Any],
    feature_order: tuple[str, ...],
) -> dict[str, float] | None:
    parameters = actual_execution.get("parameters")
    if not isinstance(parameters, Mapping):
        return None
    values: dict[str, float] = {}
    for public, (internal, _unit) in PUBLIC_PARAMETER_TO_INTERNAL_FEATURE.items():
        entry = parameters.get(public)
        if not isinstance(entry, Mapping):
            return None
        number = _finite_number(entry.get("value"))
        if number is None:
            return None
        values[internal] = number
    return {feature: values[feature] for feature in feature_order}


def extract_real_training_observations(
    scientific_state_facts: Mapping[str, Any],
    profile: Mapping[str, Any],
) -> tuple[Mapping[str, Any], list[dict[str, Any]]]:
    """Build BO-internal numeric observations from canonical real project facts only.

    Only real executions (``status=executed`` and ``validity=valid``) from the
    current experimental system, and only final valid measurements matching the
    profile target metric, become training observations. Recommendations,
    planned execution, invalid/partial execution, provisional/invalid
    measurements, and cross-system history are never promoted.
    """

    current_ref, current_context, history = resolve_scientific_state_facts(
        scientific_state_facts
    )
    feature_order = tuple(str(item) for item in profile["feature_order"])
    target_metric = str(profile["objective"]["target_metric"]).strip().casefold()
    project: list[dict[str, Any]] = []

    for row in history:
        if str(row.get("experimental_system_context_ref") or "") != current_ref:
            # Historical experiments from another formal experimental system remain in
            # state but are not silently mixed into the current BO training set.
            continue
        actual = row.get("actual_execution")
        if not isinstance(actual, Mapping):
            continue
        if str(actual.get("status") or "") != "executed":
            continue
        if str(actual.get("validity") or "") != "valid":
            continue
        parameters = _actual_parameters_as_internal_features(actual, feature_order)
        if parameters is None:
            continue

        experiment_ref = str(row.get("experiment_ref") or "").strip()
        measurements = row.get("measurements")
        if not isinstance(measurements, list):
            continue
        for measurement in measurements:
            if not isinstance(measurement, Mapping):
                continue
            if str(measurement.get("metric_key") or "").strip().casefold() != target_metric:
                continue
            if str(measurement.get("status") or "") != "final":
                continue
            if str(measurement.get("validity") or "") != "valid":
                continue
            objective_value = _finite_number(measurement.get("value"))
            if objective_value is None:
                continue
            measurement_ref = str(measurement.get("measurement_ref") or "").strip()
            project.append(
                {
                    "observation_id": f"{experiment_ref}:{measurement_ref}",
                    "parameters": parameters,
                    "objective_value": objective_value,
                }
            )

    return current_context, project
