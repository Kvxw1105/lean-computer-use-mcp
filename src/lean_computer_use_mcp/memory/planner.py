"""Value planning for recalled workflows.

A learned ``type_text`` component stores ``{value}`` instead of the concrete
string, so one component serves "12" and "18". When a plan is built from
memory, those steps carry ``value_placeholder``; the planner asks the user
only for the varying values (or accepts ``--value`` flags) and substitutes
them before execution. Everything else in the plan is reused as-is, so a
repeated task costs one prompt instead of re-discovery.
"""

from __future__ import annotations

from dataclasses import replace

from lean_computer_use_mcp.memory.retrieve import RecallPlan
from lean_computer_use_mcp.record.model import RecordedStep


def placeholder_indices(plan: RecallPlan) -> list[int]:
    """1-based step indices whose value is a template placeholder."""
    return [
        index
        for index, step in enumerate(plan.steps, start=1)
        if step.value_placeholder
    ]


def fill_plan_values(plan: RecallPlan, values: list[str]) -> RecallPlan:
    """Return a copy of the plan with placeholder values filled in order.

    Raises ``ValueError`` when the number of values does not match the number
    of placeholder steps, so a forgotten value never silently types nothing.
    """
    indices = placeholder_indices(plan)
    if len(values) != len(indices):
        raise ValueError(
            f"plan has {len(indices)} value placeholder(s) "
            f"(steps {indices}) but {len(values)} value(s) were given"
        )
    steps: list[RecordedStep] = []
    values_iter = iter(values)
    for step in plan.steps:
        if step.value_placeholder:
            steps.append(
                replace(
                    step, value=next(values_iter), value_placeholder=False
                )
            )
        else:
            steps.append(step)
    return RecallPlan(
        kind=plan.kind,
        app=plan.app,
        steps=steps,
        score=plan.score,
        tentative=plan.tentative,
        source=plan.source,
    )
