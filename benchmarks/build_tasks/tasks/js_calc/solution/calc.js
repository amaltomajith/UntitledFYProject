function evaluate(src) {
  const tokens = src.match(/\d+(?:\.\d+)?|\.\d+|[-+*/%()]|\S/g) || [];
  let i = 0;
  const fail = () => { throw new SyntaxError('bad expression'); };
  function expr() {
    let v = term();
    while (tokens[i] === '+' || tokens[i] === '-') v = tokens[i++] === '+' ? v + term() : v - term();
    return v;
  }
  function term() {
    let v = unary();
    while (['*', '/', '%'].includes(tokens[i])) {
      const op = tokens[i++];
      const r = unary();
      if (op !== '*' && r === 0) throw new RangeError('division by zero');
      v = op === '*' ? v * r : op === '/' ? v / r : v % r;
    }
    return v;
  }
  function unary() {
    if (tokens[i] === '-') { i++; return -unary(); }
    if (tokens[i] === '+') { i++; return unary(); }
    if (tokens[i] === '(') {
      i++;
      const v = expr();
      if (tokens[i++] !== ')') fail();
      return v;
    }
    if (i < tokens.length && /^[\d.]/.test(tokens[i])) return parseFloat(tokens[i++]);
    return fail();
  }
  const out = expr();
  if (i !== tokens.length) fail();
  return out;
}
module.exports = { evaluate };
