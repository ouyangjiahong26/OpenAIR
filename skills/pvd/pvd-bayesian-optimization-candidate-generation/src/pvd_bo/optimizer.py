from __future__ import annotations

import warnings
from dataclasses import asdict, dataclass
from typing import Any, Mapping

import numpy as np
from sklearn.exceptions import ConvergenceWarning
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import ConstantKernel, Matern, WhiteKernel
from sklearn.preprocessing import StandardScaler

from .search_space import (
    diversity_group_keys,
    exclude_observed,
    generate_constrained_grid,
    normalize_by_bounds,
)
from .selection import greedy_diverse_selection, tiered_selection


@dataclass(frozen=True)
class OptimizerDiagnostics:
    training_count: int
    raw_grid_count: int
    constrained_grid_count: int
    excluded_by_constraints: int
    excluded_observed_count: int
    candidate_count_before_distance_filter: int
    distance_eligible_count: int
    distance_filter_applied: bool
    fitted_kernel: str
    fit_warning_count: int
    fit_warning_messages: tuple[str, ...]
    target_mean: float
    target_std: float
    kappa: float
    top_n: int
    diversity_min_distance: float
    diversity_penalty_weight: float
    diversity_group_by: tuple[str, ...]
    max_per_group: int | None
    high_ucb_pool_size: int

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["fit_warning_messages"] = list(self.fit_warning_messages)
        data["diversity_group_by"] = list(self.diversity_group_by)
        return data


def _build_model(profile: Mapping[str, Any]) -> GaussianProcessRegressor:
    gpr = profile["gpr"]
    initial = gpr["length_scale_initial"]
    if isinstance(initial, list):
        length_scale = np.asarray(initial, dtype=float)
    else:
        length_scale = float(initial)
    kernel = (
        ConstantKernel(1.0, tuple(gpr["constant_bounds"]))
        * Matern(
            length_scale=length_scale,
            length_scale_bounds=tuple(gpr["length_scale_bounds"]),
            nu=float(gpr.get("nu", 2.5)),
        )
        + WhiteKernel(
            noise_level=float(gpr["white_noise_initial"]),
            noise_level_bounds=tuple(gpr["white_noise_bounds"]),
        )
    )
    return GaussianProcessRegressor(
        kernel=kernel,
        alpha=float(gpr["alpha"]),
        normalize_y=bool(gpr["normalize_y"]),
        n_restarts_optimizer=int(gpr["optimizer_restarts"]),
        random_state=int(profile["random_seed"]),
    )


def _predict_unscaled(
    model: GaussianProcessRegressor,
    x_scaler: StandardScaler,
    y_scaler: StandardScaler,
    candidates: np.ndarray,
    chunk_size: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return mean, total std and latent std in original target units.

    The latent std removes the fitted WhiteKernel observation noise so the
    acquisition function is driven by model uncertainty about the underlying
    process, not by irreducible measurement noise.
    """
    white_noise_scaled = float(model.kernel_.k2.noise_level)
    y_scale = float(y_scaler.scale_[0])
    means: list[np.ndarray] = []
    total_stds: list[np.ndarray] = []
    latent_stds: list[np.ndarray] = []
    for start in range(0, len(candidates), chunk_size):
        transformed = x_scaler.transform(candidates[start : start + chunk_size])
        mean_scaled, std_scaled = model.predict(transformed, return_std=True)
        latent_scaled = np.sqrt(np.maximum(std_scaled**2 - white_noise_scaled, 0.0))
        means.append(y_scaler.inverse_transform(mean_scaled.reshape(-1, 1)).ravel())
        total_stds.append(std_scaled * y_scale)
        latent_stds.append(latent_scaled * y_scale)
    return np.concatenate(means), np.concatenate(total_stds), np.concatenate(latent_stds)


def _utility_sign(profile: Mapping[str, Any]) -> float:
    return 1.0 if profile["objective"]["direction"] == "maximize" else -1.0


def _observed_distances(
    candidates: np.ndarray,
    x_train: np.ndarray,
    profile: Mapping[str, Any],
    chunk_size: int,
) -> np.ndarray:
    from sklearn.neighbors import NearestNeighbors

    normalized_candidates = normalize_by_bounds(candidates, profile)
    normalized_observed = normalize_by_bounds(x_train, profile)
    neighbors = NearestNeighbors(n_neighbors=1).fit(normalized_observed)
    distances = np.empty(len(candidates), dtype=float)
    for start in range(0, len(candidates), chunk_size):
        stop = min(start + chunk_size, len(candidates))
        distances[start:stop] = neighbors.kneighbors(
            normalized_candidates[start:stop], return_distance=True
        )[0].ravel()
    return distances


def generate_candidates(
    x_train: np.ndarray,
    y_target: np.ndarray,
    profile: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    feature_order = tuple(str(item) for item in profile["feature_order"])

    sign = _utility_sign(profile)
    y_utility = y_target * sign
    x_scaler = StandardScaler()
    y_scaler = StandardScaler()
    x_scaled = x_scaler.fit_transform(x_train)
    y_scaled = y_scaler.fit_transform(y_utility.reshape(-1, 1)).ravel()

    model = _build_model(profile)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", category=ConvergenceWarning)
        model.fit(x_scaled, y_scaled)

    # In-sample fit of the freshly trained model: evidence that this run
    # trained on the current data, not a generalization estimate.
    fitted_utility = y_scaler.inverse_transform(
        model.predict(x_scaled).reshape(-1, 1)
    ).ravel()
    residual = fitted_utility - y_utility
    total_variance = float(np.sum((y_utility - y_utility.mean()) ** 2))
    train_r2 = (
        1.0 - float(np.sum(residual**2)) / total_variance if total_variance > 0 else 0.0
    )
    train_mae = float(np.mean(np.abs(residual)))

    grid, raw_count, excluded_constraints = generate_constrained_grid(profile)
    candidates, excluded_observed = exclude_observed(grid, x_train)
    if not len(candidates):
        raise ValueError("all configured grid points have already been observed")

    diversity = profile["diversity"]
    minimum_distance = float(diversity["min_distance_from_observed"])
    penalty_weight = float(diversity["greedy_penalty_weight"])
    pool_limit = int(diversity["high_ucb_pool_size"])
    group_by = tuple(str(item) for item in diversity.get("group_by", []))
    max_per_group = int(diversity["max_per_group"]) if group_by else None
    top_n = int(profile["top_n"])
    chunk_size = int(profile["prediction_chunk_size"])
    if top_n <= 0 or chunk_size <= 0 or pool_limit <= 0:
        raise ValueError("invalid optimizer sizes")

    history_distance = _observed_distances(candidates, x_train, profile, chunk_size)
    history_band = (profile.get("selection") or {}).get("distance_to_history") or {}
    band_min = float(history_band.get("min", 0.0))
    band_max = float(history_band.get("max", float("inf")))
    eligible = (history_distance >= band_min) & (history_distance <= band_max)

    candidate_count_before_distance_filter = len(candidates)
    distance_eligible_count = int(eligible.sum())
    distance_filter_applied = distance_eligible_count >= top_n
    if distance_filter_applied:
        candidates = candidates[eligible]
        history_distance = history_distance[eligible]

    utility_mean, total_std, latent_std = _predict_unscaled(
        model, x_scaler, y_scaler, candidates, chunk_size
    )
    kappa = float(profile["acquisition"]["kappa"])
    # "total" keeps the original v1 behavior; the production GP63 profile opts
    # into "latent" (WhiteKernel observation noise removed) explicitly.
    uncertainty_kind = str(profile["acquisition"].get("uncertainty", "total"))
    if uncertainty_kind not in {"latent", "total"}:
        raise ValueError(f"unsupported acquisition uncertainty: {uncertainty_kind}")
    acquisition_std = latent_std if uncertainty_kind == "latent" else total_std
    acquisition = utility_mean + kappa * acquisition_std

    selection_kind = str((profile.get("selection") or {}).get("kind", "greedy"))
    pool_normalized = normalize_by_bounds(candidates, profile)
    pool_count = min(pool_limit, len(candidates))
    pool_indices = np.argsort(-acquisition, kind="stable")[:pool_count]
    uncertainty_label = (
        f"{uncertainty_kind} posterior std excluding WhiteKernel"
        if uncertainty_kind == "latent"
        else "total posterior std"
    )
    if selection_kind == "tiered_4_4_2":
        gas_columns = [feature_order.index(name) for name in ("n2_sccm", "ar_sccm")]
        selected, roles, tier_detail = tiered_selection(
            utility_mean[pool_indices],
            latent_std[pool_indices],
            acquisition[pool_indices],
            history_distance[pool_indices],
            pool_normalized[pool_indices],
            pool_normalized[pool_indices][:, gas_columns],
            profile,
        )
        extra_diagnostics = {
            "selection_kind": "tiered_4_4_2",
            "selection_detail": str(tier_detail.get("selection", "")),
            "uncertainty_for_acquisition": uncertainty_label,
            "core_pool_count": int(tier_detail.get("core_pool_count", 0)),
            "diverse_pool_count": int(tier_detail.get("diverse_pool_count", 0)),
            "remote_count": int(tier_detail.get("remote_count", 0)),
        }
    else:
        pool_candidates = candidates[pool_indices]
        pool_acquisition = acquisition[pool_indices]
        group_keys = diversity_group_keys(pool_candidates, profile)
        selected = greedy_diverse_selection(
            pool_acquisition,
            pool_normalized[pool_indices],
            min(top_n, pool_count),
            penalty_weight,
            group_keys,
            max_per_group,
        )
        roles = ["exploration_exploitation_tradeoff_with_diversity"] * len(selected)
        extra_diagnostics = {
            "selection_kind": "greedy",
            "uncertainty_for_acquisition": uncertainty_label,
        }

    selected_candidates = candidates[pool_indices][selected]
    selected_utility_mean = utility_mean[pool_indices][selected]
    selected_latent_std = latent_std[pool_indices][selected]
    selected_total_std = total_std[pool_indices][selected]
    selected_acquisition = acquisition[pool_indices][selected]

    output_candidates: list[dict[str, Any]] = []
    for rank, (row, mean, latent, total, score, role) in enumerate(
        zip(
            selected_candidates,
            selected_utility_mean,
            selected_latent_std,
            selected_total_std,
            selected_acquisition,
            roles,
            strict=True,
        ),
        start=1,
    ):
        output_candidates.append(
            {
                "candidate_rank": rank,
                "parameters": {
                    feature: float(row[index])
                    for index, feature in enumerate(feature_order)
                },
                "predicted_target": float(mean * sign),
                "predictive_std": float(total),
                "predictive_std_latent": float(latent),
                "acquisition_score": float(score),
                "selection_purpose": str(role),
                "selection_role": str(role),
            }
        )

    diagnostics = OptimizerDiagnostics(
        training_count=len(x_train),
        raw_grid_count=raw_count,
        constrained_grid_count=len(grid),
        excluded_by_constraints=excluded_constraints,
        excluded_observed_count=excluded_observed,
        candidate_count_before_distance_filter=candidate_count_before_distance_filter,
        distance_eligible_count=distance_eligible_count,
        distance_filter_applied=distance_filter_applied,
        fitted_kernel=str(model.kernel_),
        fit_warning_count=len(caught),
        fit_warning_messages=tuple(str(item.message) for item in caught),
        target_mean=float(np.mean(y_target)),
        target_std=float(np.std(y_target, ddof=1)) if len(y_target) > 1 else 0.0,
        kappa=kappa,
        top_n=len(selected),
        diversity_min_distance=minimum_distance,
        diversity_penalty_weight=penalty_weight,
        diversity_group_by=group_by,
        max_per_group=max_per_group,
        high_ucb_pool_size=pool_count,
    ).to_dict()
    diagnostics.update(extra_diagnostics)
    diagnostics.update({
        "train_r2": train_r2,
        "train_mae": train_mae,
        "optimizer_restarts": int(profile["gpr"]["optimizer_restarts"]),
    })
    return output_candidates, diagnostics
