def merge(intervals):
    out = []
    for s, e in sorted(intervals):
        if s >= e:
            raise ValueError((s, e))
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out

def free_slots(busy, day_start, day_end, min_length=1):
    slots, at = [], day_start
    for s, e in merge(busy):
        if s > at and min(s, day_end) - at >= min_length:
            slots.append((at, min(s, day_end)))
        at = max(at, e)
    if day_end - at >= min_length and at < day_end:
        slots.append((at, day_end))
    return slots

def first_common_slot(calendars, length, day_start, day_end):
    busy = [i for c in calendars for i in c]
    for s, e in free_slots(busy, day_start, day_end, length):
        return (s, s + length)
    return None
