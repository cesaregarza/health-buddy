"""Display/report identity for comparable logged strength work.

Names and stack-basis spellings may change without changing equipment. Keep
verified different machines distinct, even when their nominal loads coincide.
The prescription engine intentionally retains its own exact-machine matching.
"""

from __future__ import annotations

EXERCISE_NAMES = {
    "chest_press": "Chest press",
    "lat_pulldown": "Lat pulldown",
    "seated_row": "Seated row",
    "shoulder_press": "Shoulder press",
    "abdominal_crunch": "Abdominal crunch",
    "dumbbell_curl": "Dumbbell curl",
    "triceps_pressdown": "Triceps pressdown",
    "triceps_extension": "Triceps extension",
    "leg_press": "Leg press",
    "seated_leg_curl": "Seated leg curl",
    "leg_extension": "Leg extension",
    "calf_extension": "Calf extension",
    "goblet_squat": "Goblet squat",
    "romanian_deadlift": "Romanian deadlift",
}

# Physical identity and comparison status come from supplied workspace inputs.
# Generic labels never prove that two records used the same machine.
EQUIPMENT_ALIASES: dict[str, dict[str, str]] = {}
UNVERIFIED_COMPARISONS: frozenset[tuple[str, str]] = frozenset()

EQUIPMENT_DISPLAY = {
    "independent_arm_chest_press_machine": "independent-arm machine",
    "independent_arm_row_machine": "independent-arm machine",
    "chest_press_machine": "total-stack machine",
    "seated_row_machine": "total-stack machine",
}

TOTAL_STACK_BASES = frozenset({"machine_stack", "machine_stack_total", "total_stack"})
GENERIC_EQUIPMENT = frozenset({"", "machine", "unknown", "unrecorded", "unspecified"})


def comparison_is_unverified(
    exercise: str, equipment: str,
    unverified: frozenset[tuple[str, str]] = frozenset(),
) -> bool:
    """A generic label never establishes a stable physical machine identity."""
    return equipment.strip().casefold() in GENERIC_EQUIPMENT or (exercise, equipment) in unverified


def exercise_key(name: str) -> str:
    slug = (name or "").strip().lower().replace("-", "_").replace(" ", "_")
    return {"seated_dumbbell_curl": "dumbbell_curl"}.get(slug, slug)


def equipment_key(exercise: str, equipment: str, aliases: dict[str, dict[str, str]] | None = None) -> str:
    raw = (equipment or "").strip()
    return (aliases or {}).get(exercise, {}).get(raw, raw)


def basis_key(basis: str) -> str:
    raw = (basis or "").strip()
    return "total_stack" if raw in TOTAL_STACK_BASES else raw


def comparable_identity(
    exercise: str, equipment: str, basis: str, *,
    equipment_aliases: dict[str, dict[str, str]] | None = None,
) -> tuple[str, str, str]:
    key = exercise_key(exercise)
    return key, equipment_key(key, equipment, equipment_aliases), basis_key(basis)


def equipment_label(exercise: str, equipment: str) -> str:
    return EQUIPMENT_DISPLAY.get(equipment, equipment.replace("_", " ") or "machine")
