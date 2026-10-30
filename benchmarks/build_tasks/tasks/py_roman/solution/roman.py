PAIRS = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"),
         (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]

def to_roman(n):
    if not isinstance(n, int) or not 1 <= n <= 3999:
        raise ValueError(n)
    out = ""
    for value, sym in PAIRS:
        while n >= value:
            out += sym
            n -= value
    return out

TABLE = {}

def from_roman(s):
    if not TABLE:
        TABLE.update({to_roman(n): n for n in range(1, 4000)})
    if s not in TABLE:
        raise ValueError(s)
    return TABLE[s]
