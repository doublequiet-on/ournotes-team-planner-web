"""Conservative AP bounds; exact answers still use every audited skill order.

Binary32 command increments are represented in integer units of 2**-149.
The envelope allows independent, repeated Snap choices and includes a rounding
error allowance. It can only rule out an impossible grade, never certify one.
"""
from collections import Counter
import math

import card_power as cp
import manual_score_reference as ms

QUANTUM = 1 << 149


def increments(slot):
    result = []
    for effect in slot["active_live_effects"]:
        kind = effect["skill_effect_type"]
        if kind not in (2000, 2004) or effect["effect_value"] < 0:
            raise ValueError("AP envelope requires supported positive score effects")
        targets = [0] if kind == 2000 else effect.get("judgement_targets")
        if not isinstance(targets, list) or not targets or (kind == 2004 and any(type(t) is not int or t not in (3, 4, 5, 6) for t in targets)):
            raise ValueError("AP envelope requires explicit covered judgement targets")
        duration = cp._float32(cp._float32(effect["activation_ms"]) + cp._float32(slot["extension_ms"]))
        value = cp._float32(cp._float32(effect["effect_value"]) / cp._float32(10000))
        scaled = cp._float32(value * cp._float32(100000))
        amount = math.floor(scaled) if kind == 2000 else round(scaled)
        q = cp._float32(cp._float32(amount) / cp._float32(100000))
        numerator, denominator = q.as_integer_ratio()
        for target in targets:
            if target in (0, 5):
                result.append((math.ceil(duration), numerator * (QUANTUM // denominator)))
    return result


def pool_upper_signature(chart, options):
    """Upper-bound a fixed order over all bindings, hence also min(120 orders).

    At each time independently choose the strongest slot for each performer.
    N full binary32 ULPs at twice the absolute command sum bound accumulated
    rounding; a second allowance preserves a conservative conversion at ties.
    """
    parts = [[increments(slot) for slot in pool] for pool in options]
    # Both accumulators round independently; score.rs rounds their final sum
    # once more. Include that addition even for a pool of AP-inactive effects.
    n = 1 + 2 * sum(max(len(rows) for rows in pool) for pool in parts)
    absolute = QUANTUM + 2 * sum(max(sum(q for _, q in rows) for rows in pool) for pool in parts)
    exponent = (2 * absolute - 1).bit_length() - 149
    ulp = 1 << (exponent - 23 + 149)
    if n > 10000 or n * ulp > absolute:
        raise ValueError("AP envelope rounding bound is unavailable")
    error = 2 * n * ulp
    events = []
    for position, pool in enumerate(parts):
        start = chart["skill_time_ms"][position]
        endings = [[(min(start + duration, chart["music_length_ms"]), q) for duration, q in rows]
                   for rows in pool]
        if any(end <= start for rows in endings for end, _ in rows):
            raise ValueError("AP envelope encountered an invalid skill window")
        last = 0
        for t in sorted({start} | {end for rows in endings for end, _ in rows}):
            value = max(sum(q for end, q in rows if t < end) for rows in endings)
            events.append((t, value - last))
            last = value
    events.sort()
    counts, cursor, factor = Counter(), 0, QUANTUM
    for (t, _, op), combo in zip(chart["notes"], chart["combo_bases"]):
        while cursor < len(events) and events[cursor][0] <= t:
            factor += events[cursor][1]
            cursor += 1
        counts[chart["note_percents"][op], combo, cp._float32((factor + error) / QUANTUM)] += 1
    return tuple(counts.items())


def signature_score(chart, signature, power):
    return sum(count * ms.note_score(power, chart["level"], chart["denominator"], chart["adjustment"],
                                     pct, chart["perfect_percent"], combo, factor)
               for (pct, combo, factor), count in signature)


def power_thresholds(chart, score, upper_power):
    """Invert a monotone audited score function, retaining every rounding step."""
    result, probes = [], {}
    original_score = score
    def score(power):
        if power not in probes:
            probes[power] = original_score(power)
        return probes[power]
    maximum = score(upper_power)
    for rank in ms.RANKS.values():
        required = next(row["_requiredScore"] for row in chart["rank_thresholds"]
                        if ms.RANKS[row["_liveScoreRank"]] == rank)
        if required > maximum:
            result.append(upper_power + 1)
            continue
        low, high = 0, upper_power
        while low < high:
            middle = (low + high) // 2
            if score(middle) >= required:
                high = middle
            else:
                low = middle + 1
        result.append(low)
    return result
