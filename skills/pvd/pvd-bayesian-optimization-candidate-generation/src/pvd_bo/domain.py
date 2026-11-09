from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ApplicabilityResult:
    status: str
    profile_id: str
    profile_version: int
    issues: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["issues"] = list(self.issues)
        return data


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    try:
        return float(text)
    except ValueError:
        return None


def _number_token(text: str) -> float | None:
    match = re.search(r"\d+(?:\.\d+)?", text)
    return float(match.group()) if match else None


def _normalized_text(value: Any) -> str:
    return "".join(
        character for character in str(value).strip().lower() if character.isalnum()
    )


def _same_value(actual: Any, expected: Any, tolerance: float) -> bool:
    """Field-level same-system comparison with domain-aware normalization.

    Real requests express one system in many spellings ("Al-40%Sc" vs
    "40%AlSc", substrate "Mo/Si(100)" vs "Mo", angle 30 vs "内偏30度"), so the
    gate compares in three steps: pure numeric values numerically; then
    normalized containment/equality (with a numeric-token guard so "Si(100)"
    never matches "Si(111)"); then, when both sides carry a number and their
    non-digit skeletons are compatible, numeric equality of the embedded
    numbers. A genuinely different system ("Si" vs "Mo", "内偏30度" vs
    "外偏30度") still mismatches.
    """
    actual_number = _numeric(actual)
    expected_number = _numeric(expected)
    if actual_number is not None and expected_number is not None:
        return abs(actual_number - expected_number) <= tolerance
    actual_text = _normalized_text(actual)
    expected_text = _normalized_text(expected)
    if not actual_text or not expected_text:
        return False
    actual_skeleton = "".join(c for c in actual_text if not c.isdigit())
    expected_skeleton = "".join(c for c in expected_text if not c.isdigit())
    skeleton_compatible = (
        actual_skeleton == expected_skeleton
        or (expected_skeleton and expected_skeleton in actual_skeleton)
        or not expected_skeleton
        or not actual_skeleton
    )
    actual_token = _number_token(actual_text)
    expected_token = _number_token(expected_text)
    if actual_text == expected_text:
        return True
    if expected_text in actual_text or actual_text in expected_text:
        if actual_token is not None and expected_token is not None:
            return abs(actual_token - expected_token) <= tolerance
        return True
    if skeleton_compatible and actual_token is not None and expected_token is not None:
        return abs(actual_token - expected_token) <= tolerance
    return sorted(actual_text) == sorted(expected_text)


def evaluate_process_context(
    process_context: Mapping[str, Any] | None,
    profile: Mapping[str, Any],
) -> ApplicabilityResult:
    """Compare the current experimental system against the frozen profile domain.

    Every ``required_process_context`` field must be present with a matching
    value. Missing fields make the check indeterminate (the system may still
    match once the facts arrive); a present-but-different field is a hard
    mismatch (ineligible), because the profile's base dataset and fitted
    kernel describe that other system only.
    """
    domain = profile.get("applicability_domain") or {}
    required = domain.get("required_process_context") or {}
    tolerance = float(domain.get("numeric_tolerance", 1e-8))

    missing: list[str] = []
    mismatched: list[str] = []
    context = process_context if isinstance(process_context, Mapping) else {}
    for key, expected in required.items():
        actual = context.get(key)
        if actual is None or (isinstance(actual, str) and not actual.strip()):
            missing.append(f"missing_process_context:{key}")
        elif not _same_value(actual, expected, tolerance):
            mismatched.append(f"process_context_mismatch:{key}")

    issues = [*missing, *mismatched]
    if not issues:
        status = "eligible"
    elif mismatched:
        status = "ineligible"
    else:
        status = "indeterminate"
    return ApplicabilityResult(
        status=status,
        profile_id=str(profile["profile_id"]),
        profile_version=int(profile["profile_version"]),
        issues=tuple(issues),
    )


__all__ = ["ApplicabilityResult", "evaluate_process_context"]
