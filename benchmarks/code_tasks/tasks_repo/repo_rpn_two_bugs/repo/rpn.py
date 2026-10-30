"""Reverse-Polish-notation calculator."""

OPS = {
    "+": lambda a, b: a + b,
    "-": lambda a, b: a - b,
    "*": lambda a, b: a * b,
    "/": lambda a, b: a // b,
}


def tokenize(expr):
    """Split an RPN expression into tokens; numbers become floats."""
    tokens = []
    for part in expr.split():
        if part in OPS:
            tokens.append(part)
        else:
            tokens.append(float(part))
    return tokens


def evaluate(expr):
    """Evaluate an RPN expression such as '3 4 + 2 *'."""
    stack = []
    for tok in tokenize(expr):
        if isinstance(tok, float):
            stack.append(tok)
            continue
        if len(stack) < 2:
            raise ValueError(f"not enough operands for {tok!r}")
        a = stack.pop()
        b = stack.pop()
        stack.append(OPS[tok](a, b))
    if len(stack) != 1:
        raise ValueError("malformed expression")
    return stack[0]
