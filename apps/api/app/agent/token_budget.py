from __future__ import annotations

from dataclasses import dataclass
from math import ceil


def estimate_token_count(text: str) -> int:
    normalized = text.strip()
    if not normalized:
        return 0
    return max(1, ceil(len(normalized) / 4))


def truncate_text_to_token_budget(text: str, token_budget: int) -> str:
    if token_budget <= 0:
        return ""
    normalized = text.strip()
    if not normalized:
        return ""
    if estimate_token_count(normalized) <= token_budget:
        return normalized
    character_budget = max(token_budget * 4, 1)
    if len(normalized) <= character_budget:
        return normalized
    if character_budget <= 3:
        return normalized[:character_budget]
    return f"{normalized[: character_budget - 3].rstrip()}..."


@dataclass(frozen=True)
class TokenBudgetComponentRequest:
    name: str
    requested_tokens: int
    floor_tokens: int = 0
    compressible: bool = True


@dataclass(frozen=True)
class TokenBudgetAllocation:
    total_budget: int
    requested_total: int
    allocated_total: int
    remaining_budget: int
    component_tokens: dict[str, int]
    compressed_components: list[str]
    dropped_components: list[str]
    compression_order: list[str]
    fits_requested_budget: bool

    def to_state(self) -> dict[str, object]:
        return {
            "total_budget": self.total_budget,
            "requested_total": self.requested_total,
            "allocated_total": self.allocated_total,
            "remaining_budget": self.remaining_budget,
            "component_tokens": dict(self.component_tokens),
            "compressed_components": list(self.compressed_components),
            "dropped_components": list(self.dropped_components),
            "compression_order": list(self.compression_order),
            "fits_requested_budget": self.fits_requested_budget,
        }


DEFAULT_COMPRESSION_ORDER: tuple[str, ...] = (
    "retrieval",
    "history",
    "memory",
    "capability_schema",
    "capability_prompt",
    "task_local_detail",
)


def allocate_token_budget(
    *,
    total_budget: int,
    components: list[TokenBudgetComponentRequest],
    compression_order: tuple[str, ...] = DEFAULT_COMPRESSION_ORDER,
) -> TokenBudgetAllocation:
    normalized_total_budget = max(total_budget, 0)
    requested_by_name = {
        component.name: max(component.requested_tokens, 0) for component in components
    }
    floor_by_name = {
        component.name: min(max(component.floor_tokens, 0), requested_by_name[component.name])
        for component in components
    }
    compressible_by_name = {component.name: component.compressible for component in components}
    allocated = dict(requested_by_name)
    requested_total = sum(requested_by_name.values())
    overflow = max(requested_total - normalized_total_budget, 0)
    compressed_components: list[str] = []

    for component_name in compression_order:
        if overflow <= 0:
            break
        if not compressible_by_name.get(component_name, True):
            continue
        current_tokens = allocated.get(component_name, 0)
        floor_tokens = floor_by_name.get(component_name, 0)
        reducible_tokens = max(current_tokens - floor_tokens, 0)
        if reducible_tokens <= 0:
            continue
        reduction = min(reducible_tokens, overflow)
        allocated[component_name] = current_tokens - reduction
        overflow -= reduction
        compressed_components.append(component_name)

    if overflow > 0:
        for component in components:
            if overflow <= 0:
                break
            if component.name in compression_order:
                continue
            if not component.compressible:
                continue
            current_tokens = allocated.get(component.name, 0)
            floor_tokens = floor_by_name.get(component.name, 0)
            reducible_tokens = max(current_tokens - floor_tokens, 0)
            if reducible_tokens <= 0:
                continue
            reduction = min(reducible_tokens, overflow)
            allocated[component.name] = current_tokens - reduction
            overflow -= reduction
            compressed_components.append(component.name)

    allocated_total = sum(allocated.values())
    dropped_components = [name for name, tokens in allocated.items() if tokens == 0]
    return TokenBudgetAllocation(
        total_budget=normalized_total_budget,
        requested_total=requested_total,
        allocated_total=allocated_total,
        remaining_budget=max(normalized_total_budget - allocated_total, 0),
        component_tokens=allocated,
        compressed_components=compressed_components,
        dropped_components=dropped_components,
        compression_order=list(compression_order),
        fits_requested_budget=requested_total <= normalized_total_budget,
    )
