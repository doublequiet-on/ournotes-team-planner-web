"""Exact AP signatures by command intervals, preserving both binary32 channels."""
from bisect import bisect_left
from collections import Counter, defaultdict
import itertools

import manual_score_reference as ms


def note_index(chart):
    times = defaultdict(list)
    for (time, _, op), base in zip(chart["notes"], chart["combo_bases"], strict=True):
        times[chart["note_percents"][op], base].append(time)
    return tuple((key, tuple(value)) for key, value in times.items())


def signature(chart, slots, order, index):
    commands = ms.factor_commands(chart["skill_time_ms"], slots, order,
                                  chart["music_length_ms"], channels=True)
    positions = [0] * len(index)
    counts = Counter()
    note, perfect = ms.f32(1), ms.f32(0)

    def collect(end=None):
        factor = ms.f32(note + perfect)
        for i, ((percent, base), times) in enumerate(index):
            stop = len(times) if end is None else bisect_left(times, end, positions[i])
            if stop > positions[i]:
                counts[percent, base, factor] += stop - positions[i]
                positions[i] = stop

    for time, group in itertools.groupby(commands, key=lambda command: command[0]):
        # Strictly before t: all commands at t precede notes at t.
        collect(time)
        for command in group:
            amount = ms.f32(ms.f32(command[2]) / ms.f32(100000))
            if command[3] == 0:
                if amount != 0:
                    note = ms.f32(note + amount)
            else:
                perfect = ms.f32(perfect + amount)
    collect()
    return tuple(counts.items())
