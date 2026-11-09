from __future__ import annotations

from typing import Any, Mapping

import numpy as np


def greedy_diverse_selection(
    acquisition: np.ndarray,
    normalized_candidates: np.ndarray,
    n_select: int,
    penalty_weight: float,
    group_keys: np.ndarray | None,
    max_per_group: int | None,
) -> np.ndarray:

    scores = acquisition.astype(float).copy()
    available = np.ones(len(scores), dtype=bool)
    selected: list[int] = []

    for _ in range(min(n_select, len(scores))):
        available_indices = np.flatnonzero(available)
        if not len(available_indices):
            break
        best_index = int(available_indices[np.argmax(scores[available_indices])])
        selected.append(best_index)
        available[best_index] = False

        if group_keys is not None:
            selected_keys = group_keys[np.asarray(selected)]
            current_key = group_keys[best_index]
            if int(np.all(selected_keys == current_key, axis=1).sum()) >= int(max_per_group):
                available[np.all(group_keys == current_key, axis=1)] = False

        remaining = np.flatnonzero(available)
        if not len(remaining):
            continue
        distances = np.linalg.norm(
            normalized_candidates[remaining] - normalized_candidates[best_index],
            axis=1,
        )
        normalized_distances = (distances - distances.min()) / (
            distances.max() - distances.min() + 1e-10
        )
        near_penalty = 1.0 - normalized_distances
        scores[remaining] *= 1.0 - penalty_weight * near_penalty

    return np.asarray(selected, dtype=int)


def _minmax(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    low, high = float(values.min()), float(values.max())
    if high <= low:
        return np.ones_like(values)
    return (values - low) / (high - low)


def _min_distance_to_selected(
    coordinates: np.ndarray, indices: np.ndarray, selected: np.ndarray
) -> np.ndarray:
    if not len(selected):
        return np.full(len(indices), np.inf)
    differences = coordinates[indices][:, None, :] - coordinates[selected][None, :, :]
    return np.sqrt((differences**2).sum(axis=2)).min(axis=1)


def tiered_selection(
    pool_utility_mean: np.ndarray,
    pool_latent_std: np.ndarray,
    pool_acquisition: np.ndarray,
    pool_history_distance: np.ndarray,
    pool_coordinates: np.ndarray,
    pool_gas_coordinates: np.ndarray,
    profile: Mapping[str, Any],
) -> tuple[np.ndarray, list[str], dict[str, Any]]:
    """Port of the production GP63 tiered ``4 high-core + 4 diverse + 2 remote`` batch.

    All arrays are already restricted to the distance-eligible candidate pool and
    share its row order; ``pool_utility_mean`` is in utility orientation
    (sign-flipped for minimization profiles). Thresholds, weights and caps come
    from the frozen profile ``selection.tiered_4_4_2`` block.
    """
    tier = profile["selection"]["tiered_4_4_2"]
    top_n = int(profile["top_n"])
    core_count = min(int(tier["core_count"]), top_n)
    diverse_count = min(int(tier["diverse_count"]), top_n - core_count)

    feature_order = tuple(str(item) for item in profile["feature_order"])
    axis_caps = {
        feature_order.index(str(name)): float(value)
        for name, value in tier["first_eight_axis_caps"].items()
    }
    used_pairs: set[tuple[float, float]] = set()
    gas_pair_keys = [tuple(np.round(row, 8)) for row in pool_gas_coordinates]
    axis_values: list[dict[float, int]] = [
        {} for _ in range(pool_coordinates.shape[1])
    ]
    selected: list[int] = []
    roles: list[str] = []

    mu = pool_utility_mean.astype(float)

    def select_stage(
        pool: np.ndarray,
        count: int,
        role: str,
        weights: Mapping[str, Any],
        minimum_distance: float,
        quality_column: np.ndarray,
        apply_axis_caps: bool,
    ) -> None:
        pool = np.asarray(pool, dtype=int)
        if len(pool) < count:
            raise RuntimeError(
                f"{role}候选池只有{len(pool)}条，少于所需{count}条"
            )
        quality = _minmax(quality_column[pool])
        available = np.ones(len(pool), dtype=bool)
        weight_quality = float(weights["quality"])
        weight_parameter = float(weights["parameter_distance"])
        weight_gas = float(weights["gas_distance"])

        for _ in range(count):
            allowed: list[int] = []
            for position in np.flatnonzero(available):
                index = int(pool[position])
                if gas_pair_keys[index] in used_pairs:
                    continue
                if apply_axis_caps:
                    row = pool_coordinates[index]
                    over_cap = any(
                        axis_values[column].get(float(row[column]), 0) >= cap
                        for column, cap in axis_caps.items()
                    )
                    if over_cap:
                        continue
                if selected:
                    distance = _min_distance_to_selected(
                        pool_coordinates,
                        np.asarray([index]),
                        np.asarray(selected, dtype=int),
                    )[0]
                    if distance < minimum_distance:
                        continue
                allowed.append(int(position))
            if not allowed:
                raise RuntimeError(f"{role}的多样性约束使候选池耗尽")

            allowed_array = np.asarray(allowed, dtype=int)
            indices = pool[allowed_array]
            if not selected:
                score = quality[allowed_array]
            else:
                batch_distance = _min_distance_to_selected(
                    pool_coordinates, indices, np.asarray(selected, dtype=int)
                )
                gas_distance = _min_distance_to_selected(
                    pool_gas_coordinates, indices, np.asarray(selected, dtype=int)
                )
                score = (
                    weight_quality * quality[allowed_array]
                    + weight_parameter * _minmax(batch_distance)
                    + weight_gas * _minmax(gas_distance)
                )
            # Primary: selection score; ties broken by acquisition, then index.
            order = np.lexsort((
                indices,
                -pool_acquisition[indices],
                -score,
            ))
            chosen_position = int(allowed_array[order[0]])
            chosen_index = int(pool[chosen_position])
            available[chosen_position] = False
            selected.append(chosen_index)
            roles.append(role)
            used_pairs.add(gas_pair_keys[chosen_index])
            row = pool_coordinates[chosen_index]
            for column in range(pool_coordinates.shape[1]):
                value = float(row[column])
                axis_values[column][value] = axis_values[column].get(value, 0) + 1

    core_pool = np.flatnonzero(mu > float(tier["core_mu_min"]))
    select_stage(
        core_pool, core_count, "高值主峰探索", tier["core_weights"],
        float(tier["core_min_batch_distance"]), pool_acquisition, True,
    )

    diverse_pool = np.flatnonzero(
        (mu > float(tier["diverse_mu_min"])) & (mu <= float(tier["core_mu_min"]))
    )
    select_stage(
        diverse_pool, diverse_count, "高潜力多样化", tier["diverse_weights"],
        float(tier["diverse_min_batch_distance"]), pool_acquisition, True,
    )

    remote_count = top_n - core_count - diverse_count
    if remote_count:
        remote_pool = np.flatnonzero(
            (mu > float(tier["remote_mu_min"]))
            & (pool_history_distance >= float(tier["remote_history_min"]))
        )
        select_stage(
            remote_pool, remote_count, "远域探索", tier["remote_weights"],
            float(tier["remote_min_batch_distance"]), pool_latent_std, False,
        )

    details: dict[str, Any] = {
        "selection": "tiered 4 high-core + 4 diverse + 2 remote",
        "core_count": core_count,
        "diverse_count": diverse_count,
        "remote_count": remote_count,
        "core_pool_count": int(len(core_pool)),
        "diverse_pool_count": int(len(diverse_pool)),
        "first_eight_axis_caps": dict(axis_caps),
    }
    return np.asarray(selected, dtype=int), roles, details
