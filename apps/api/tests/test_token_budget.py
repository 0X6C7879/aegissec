from app.agent.token_budget import (
    TokenBudgetComponentRequest,
    allocate_token_budget,
    truncate_text_to_token_budget,
)


def test_allocate_token_budget_compresses_in_planned_order() -> None:
    allocation = allocate_token_budget(
        total_budget=260,
        components=[
            TokenBudgetComponentRequest(
                name="core_immutable", requested_tokens=60, floor_tokens=60, compressible=False
            ),
            TokenBudgetComponentRequest(
                name="safety_scope", requested_tokens=60, floor_tokens=60, compressible=False
            ),
            TokenBudgetComponentRequest(
                name="role_prompt", requested_tokens=30, floor_tokens=30, compressible=False
            ),
            TokenBudgetComponentRequest(
                name="task_local", requested_tokens=30, floor_tokens=30, compressible=False
            ),
            TokenBudgetComponentRequest(name="retrieval", requested_tokens=40, floor_tokens=0),
            TokenBudgetComponentRequest(name="history", requested_tokens=30, floor_tokens=0),
            TokenBudgetComponentRequest(name="memory", requested_tokens=20, floor_tokens=0),
            TokenBudgetComponentRequest(
                name="capability_schema", requested_tokens=15, floor_tokens=0
            ),
            TokenBudgetComponentRequest(
                name="capability_prompt", requested_tokens=10, floor_tokens=0
            ),
            TokenBudgetComponentRequest(
                name="task_local_detail", requested_tokens=10, floor_tokens=0
            ),
        ],
    )

    assert allocation.total_budget == 260
    assert allocation.component_tokens["core_immutable"] == 60
    assert allocation.component_tokens["safety_scope"] == 60
    assert allocation.component_tokens["role_prompt"] == 30
    assert allocation.component_tokens["task_local"] == 30
    assert allocation.component_tokens["retrieval"] == 0
    assert allocation.component_tokens["history"] == 25
    assert allocation.compressed_components[:2] == ["retrieval", "history"]
    assert allocation.fits_requested_budget is False


def test_truncate_text_to_token_budget_returns_ascii_ellipsis() -> None:
    text = "a" * 40

    truncated = truncate_text_to_token_budget(text, 4)

    assert truncated.endswith("...")
    assert len(truncated) <= 16
