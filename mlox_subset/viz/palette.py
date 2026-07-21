"""Colour ramps for the conflict visualisations.

Two ramps, chosen for what they have to communicate rather than for looks:

*Divergence* (height deltas) is symmetric around zero, because "this mod raised
the ground" and "this mod lowered it" are equally interesting and neither is
the default. Red is up and blue is down, with near-zero left almost neutral so
the eye is drawn to real movement rather than to rounding.

*Severity* (conflict counts) runs cool to hot, following the convention
``merged_lands`` established for TES3 land conflicts -- green is fine, yellow
is worth a look, red wants attention. Matching an existing tool's language
matters more here than picking a nicer palette: people read both.

Both ramps are computed rather than tabulated, so they stay smooth at any
number of steps and no lookup table has to be kept in sync with a legend.
"""

from __future__ import annotations

from collections.abc import Sequence

#: Neutral fill for a cell with data but nothing to report.
NEUTRAL = "#2c313a"

#: Outline for anything involving the user's own mods, matching the GUI's
#: orange "your custom mod" marker in the field-diff window.
MINE = "#ff9d5c"


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    """Constrain a value to a range.

    Args:
        value: The value to clamp.
        low: Lower bound.
        high: Upper bound.

    Returns:
        ``value`` limited to ``[low, high]``.
    """
    return max(low, min(high, value))


def _hex(red: float, green: float, blue: float) -> str:
    """Format three 0-1 channel values as a CSS hex colour.

    Args:
        red: Red channel, 0-1.
        green: Green channel, 0-1.
        blue: Blue channel, 0-1.

    Returns:
        A ``#rrggbb`` string.
    """
    return f"#{round(_clamp(red) * 255):02x}{round(_clamp(green) * 255):02x}{round(_clamp(blue) * 255):02x}"


def divergence(value: float, scale: float) -> str:
    """Map a signed value to a blue-neutral-red divergence colour.

    Args:
        value: The signed quantity, e.g. a height delta in world units.
        scale: The magnitude that saturates the ramp. Values beyond it clamp
            rather than wrap, so one extreme vertex cannot wash out the rest.

    Returns:
        A ``#rrggbb`` string: blue for negative, red for positive, dark
        neutral at zero.
    """
    if scale <= 0:
        return NEUTRAL
    t = _clamp(abs(value) / scale)
    # Ease the ramp so small deltas stay visible instead of vanishing into the
    # neutral end -- a 20-unit nudge matters and would otherwise be invisible
    # beside a 2000-unit cliff.
    t = t**0.6
    if value >= 0:
        return _hex(0.17 + 0.78 * t, 0.19 - 0.08 * t, 0.23 - 0.15 * t)
    return _hex(0.17 - 0.13 * t, 0.19 + 0.35 * t, 0.23 + 0.72 * t)


def severity(count: int, worst: int) -> str:
    """Map a conflict count to a green-yellow-red severity colour.

    The ramp is **linear**, deliberately. An earlier version used a square root
    to stop a few extreme cells flattening everything else to green -- and it
    did the opposite of what was wanted: with a busy load order it pushed
    ordinary cells (3 conflicts out of a worst of 30) straight into yellow, so
    the whole map read as "everything is on fire" and nothing stood out. That
    was only visible by rendering it and looking.

    The skew is real, but the fix belongs in the *scale*, not the curve: pass a
    high percentile as ``worst`` (see
    :func:`~mlox_subset.viz.conflictmap.build_conflict_map`) so one pathological
    cell clamps instead of rescaling everyone.

    Args:
        count: Conflicts on this cell.
        worst: The count that saturates the ramp.

    Returns:
        A ``#rrggbb`` string, or :data:`NEUTRAL` when there is nothing to show.
    """
    if count <= 0 or worst <= 0:
        return NEUTRAL
    t = _clamp(count / worst)
    if t < 0.5:
        u = t / 0.5
        return _hex(0.20 + 0.75 * u, 0.65 + 0.20 * u, 0.25 - 0.05 * u)
    u = (t - 0.5) / 0.5
    return _hex(0.95, 0.85 - 0.70 * u, 0.20 - 0.05 * u)


def saturation_point(counts: Sequence[int], percentile: float = 0.95) -> int:
    """Choose the count at which the severity ramp should saturate.

    Using the maximum lets a single pathological cell -- a landscape record
    touched by forty plugins -- compress everything else into the green end.
    Using a high percentile keeps the ordinary range legible and simply clamps
    the outliers, which are already the reddest thing on the map.

    Args:
        counts: Every cell's conflict count.
        percentile: Fraction of cells that should fall below the saturation
            point.

    Returns:
        The saturating count, always at least 1.
    """
    if not counts:
        return 0
    ordered = sorted(counts)
    index = min(len(ordered) - 1, int(len(ordered) * percentile))
    return max(1, ordered[index])


def legend_stops(worst: int, steps: int = 6) -> list[tuple[int, str]]:
    """Build ``(count, colour)`` pairs for a severity legend.

    Args:
        worst: The highest count on the map.
        steps: How many swatches to produce.

    Returns:
        Ascending ``(count, colour)`` pairs. Empty when there is nothing to
        show, so the caller can omit the legend entirely.
    """
    if worst <= 0 or steps <= 0:
        return []
    out: list[tuple[int, str]] = []
    for index in range(steps):
        count = max(1, round(worst * (index + 1) / steps))
        pair = (count, severity(count, worst))
        if not out or out[-1][0] != count:
            out.append(pair)
    return out
