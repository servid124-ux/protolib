"""
protolib/conditions.py

Evaluator for the condition expressions used in the `condition` fields
of containers (protodef-style: "fields.x === 1 && fields.y !== 0").

Deliberately does NOT use eval()/exec(): it parses a small, safe subset
of JS-like boolean expressions over the current container's `fields`
dict (and optionally `$root` / `$parent` to reference the parent
context).

Supported grammar:
    expr       := or_expr
    or_expr    := and_expr ( '||' and_expr )*
    and_expr   := comparison ( '&&' comparison )*
    comparison := operand ( ('===' | '!==' | '==' | '!=' | '>=' | '<=' | '>' | '<') operand )?
    operand    := path | literal | '(' expr ')'
    path       := ('fields' | '$root' | '$parent') ('.' NAME | '[' INT ']')*
    literal    := INT | FLOAT | STRING | 'true' | 'false' | 'null'

If the condition is a single `operand` with no comparison operator, its
"truthiness" is evaluated (same as in JS/Python).
"""

from __future__ import annotations

import re
from typing import Any

from .errors import ConditionError

_TOKEN_RE = re.compile(
    r"""
    \s*(?:
        (?P<op>===|!==|==|!=|>=|<=|&&|\|\||>|<|\(|\))
      | (?P<num>-?\d+\.\d+|-?\d+)
      | (?P<str>'[^']*'|"[^"]*")
      | (?P<ident>[A-Za-z_$][A-Za-z0-9_]*)
      | (?P<dot>\.)
      | (?P<lbracket>\[)
      | (?P<rbracket>\])
    )
    """,
    re.VERBOSE,
)


class _Token:
    __slots__ = ("kind", "value")

    def __init__(self, kind: str, value: str):
        self.kind = kind
        self.value = value

    def __repr__(self):
        return f"Token({self.kind!r}, {self.value!r})"


def _tokenize(expr: str) -> list[_Token]:
    tokens: list[_Token] = []
    pos = 0
    while pos < len(expr):
        m = _TOKEN_RE.match(expr, pos)
        if not m or m.end() == pos:
            stripped = expr[pos:].strip()
            if not stripped:
                break
            raise ValueError(f"unexpected token at position {pos}: {expr[pos:pos + 10]!r}")
        pos = m.end()
        kind = m.lastgroup
        value = m.group(kind)
        tokens.append(_Token(kind, value))
    return tokens


class _Parser:
    """Recursive-descent parser over the token list. Evaluates directly
    (does not build a separate AST) because the grammar is small and
    there's no need to reuse the tree."""

    def __init__(self, tokens: list[_Token], context: dict[str, Any]):
        self.tokens = tokens
        self.pos = 0
        self.context = context  # { 'fields': {...}, '$root': {...}, '$parent': {...} }

    def _peek(self) -> _Token | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def _advance(self) -> _Token:
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def _expect_op(self, value: str) -> None:
        tok = self._peek()
        if not tok or tok.value != value:
            raise ValueError(f"expected '{value}'")
        self._advance()

    def parse_expr(self) -> Any:
        return self._parse_or()

    def _parse_or(self) -> Any:
        left = self._parse_and()
        while self._peek() and self._peek().value == "||":
            self._advance()
            right = self._parse_and()
            left = bool(left) or bool(right)
        return left

    def _parse_and(self) -> Any:
        left = self._parse_comparison()
        while self._peek() and self._peek().value == "&&":
            self._advance()
            right = self._parse_comparison()
            left = bool(left) and bool(right)
        return left

    def _parse_comparison(self) -> Any:
        left = self._parse_operand()
        tok = self._peek()
        if tok and tok.kind == "op" and tok.value in ("===", "!==", "==", "!=", ">=", "<=", ">", "<"):
            op = self._advance().value
            right = self._parse_operand()
            if op == "==":
                return _loose_eq(left, right)
            if op == "!=":
                return not _loose_eq(left, right)
            if op == "===":
                return left == right
            if op == "!==":
                return left != right
            if op == ">=":
                return left >= right
            if op == "<=":
                return left <= right
            if op == ">":
                return left > right
            if op == "<":
                return left < right
        return left

    def _parse_operand(self) -> Any:
        tok = self._peek()
        if tok is None:
            raise ValueError("incomplete expression")

        if tok.value == "(":
            self._advance()
            value = self.parse_expr()
            self._expect_op(")")
            return value

        if tok.kind == "num":
            self._advance()
            return float(tok.value) if "." in tok.value else int(tok.value)

        if tok.kind == "str":
            self._advance()
            return tok.value[1:-1]

        if tok.kind == "ident":
            return self._parse_path()

        raise ValueError(f"unexpected operand: {tok!r}")

    def _parse_path(self) -> Any:
        tok = self._advance()
        name = tok.value

        if name == "true":
            return True
        if name == "false":
            return False
        if name == "null" or name == "undefined":
            return None

        if name not in self.context:
            # unknown root name (not 'fields'/'$root'/'$parent'):
            # treated as a constant-string by tolerance, same way the
            # original protodef tolerates certain standalone identifiers.
            current = None
        else:
            current = self.context[name]

        while self._peek() and self._peek().kind in ("dot", "lbracket"):
            sep = self._advance()
            if sep.kind == "dot":
                key_tok = self._advance()
                key = key_tok.value
                current = current.get(key) if isinstance(current, dict) else None
            else:  # lbracket
                idx_tok = self._advance()
                self._expect_op("]")
                idx = int(idx_tok.value)
                current = current[idx] if isinstance(current, (list, tuple)) else None

        return current


def _loose_eq(left: Any, right: Any) -> bool:
    """
    JS-style '==' coercion -- ONLY for the case relevant here: comparing
    a number (read from the wire) against a numeric string (typical
    when switch/mapping keys come from YAML/JSON as "0", "0x1f", etc.).
    Any other pair of types is compared as-is, with no weird JS-style
    coercions ([] == false, etc. -- no need to replicate the ENTIRE JS
    coercion table, just what a real binary protocol needs).
    """
    if left == right:
        return True
    if isinstance(left, bool) or isinstance(right, bool):
        return left == right
    if isinstance(left, (int, float)) and isinstance(right, str):
        return _try_parse_number(right) == left
    if isinstance(right, (int, float)) and isinstance(left, str):
        return _try_parse_number(left) == right
    return False


def _try_parse_number(s: str) -> Any:
    try:
        if s.lower().startswith("0x"):
            return int(s, 16)
        return int(s)
    except ValueError:
        try:
            return float(s)
        except ValueError:
            return object()  # matches nothing, avoids false positives


def eval_condition(expr: str, fields: dict[str, Any],
                    root: dict[str, Any] | None = None,
                    parent: dict[str, Any] | None = None) -> bool:
    """
    Evaluates a protodef-style condition expression against the current
    container's `fields` dict. `root` and `parent` are optional, for
    conditions that reference `$root.something` or `$parent.something`.
    """
    context = {"fields": fields, "$root": root or {}, "$parent": parent or {}}
    try:
        tokens = _tokenize(expr)
        parser = _Parser(tokens, context)
        result = parser.parse_expr()
        if parser.pos != len(tokens):
            raise ValueError(f"leftover tokens at the end of the expression: {expr!r}")
    except ValueError as exc:
        # Real bug: the tokenizer/parser above used to raise a raw
        # ValueError, but errors.py defines (and the README documents,
        # section 12) ConditionError as this module's own exception --
        # nobody doing `except ConditionError` was ever catching it.
        raise ConditionError(expr, str(exc)) from exc
    return bool(result)
