"""Helpers for selecting campaign script variants."""

import random


def select_script_variant(mode, scripts, rotation_index=0, rotation_order=None):
    """Return a script and persisted rotation state for the selection mode."""
    if mode not in ("sequential", "random"):
        raise ValueError("Script selection mode must be sequential or random.")
    if not scripts:
        raise ValueError("At least one script variant is required.")

    if mode == "sequential":
        index = rotation_index % len(scripts)
        return scripts[index], (index + 1) % len(scripts), rotation_order or []

    script_count = len(scripts)
    order = list(rotation_order or [])
    valid_order = sorted(order) == list(range(script_count))
    previous_last = order[-1] if valid_order and rotation_index >= script_count else None

    if not valid_order or not 0 <= rotation_index < script_count:
        if previous_last is None:
            order = list(range(script_count))
            random.shuffle(order)
        else:
            first_choices = [
                index for index in range(script_count) if index != previous_last
            ]
            first = random.choice(first_choices)
            remaining = [
                index for index in range(script_count) if index != first
            ]
            random.shuffle(remaining)
            order = [first, *remaining]
        rotation_index = 0

    index = order[rotation_index]
    return scripts[index], rotation_index + 1, order
