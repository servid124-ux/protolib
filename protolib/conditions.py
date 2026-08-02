"""
protolib/conditions.py

Evaluador de las expresiones de condición usadas en campos `condition` de
los containers (estilo protodef: "fields.x === 1 && fields.y !== 0").

Deliberadamente NO usa eval()/exec(): parsea un subconjunto chico y seguro
de JS-like boolean expressions sobre el dict `fields` del container actual
(y opcionalmente `$root` / `$parent` para referenciar el contexto padre).

Gramática soportada:
    expr       := or_expr
    or_expr    := and_expr ( '||' and_expr )*
    and_expr   := comparison ( '&&' comparison )*
    comparison := operand ( ('===' | '!==' | '==' | '!=' | '>=' | '<=' | '>' | '<') operand )?
    operand    := path | literal | '(' expr ')'
    path       := ('fields' | '$root' | '$parent') ('.' NAME | '[' INT ']')*
    literal    := INT | FLOAT | STRING | 'true' | 'false' | 'null'

Si la condición es un solo `operand` sin operador de comparación, se evalúa
su "truthiness" (igual que en JS/Python).
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
            raise ValueError(f"token inesperado en posición {pos}: {expr[pos:pos + 10]!r}")
        pos = m.end()
        kind = m.lastgroup
        value = m.group(kind)
        tokens.append(_Token(kind, value))
    return tokens


class _Parser:
    """Recursive-descent parser sobre la lista de tokens. Evalúa directamente
    (no construye un AST separado) porque la gramática es chica y no hace
    falta reusar el árbol."""

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
            raise ValueError(f"se esperaba '{value}'")
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
            raise ValueError("expresión incompleta")

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

        raise ValueError(f"operando inesperado: {tok!r}")

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
            # nombre de raíz desconocido (no es 'fields'/'$root'/'$parent'):
            # se interpreta como string-constante por tolerancia, igual que
            # protodef original tolera ciertos identificadores sueltos.
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
    Coerción estilo JS '==' -- SOLO para el caso relevante acá: comparar
    un número (leído del wire) contra un string numérico (típico cuando
    las keys de un switch/mapping vienen de YAML/JSON como "0", "0x1f",
    etc.). Cualquier otro par de tipos se compara tal cual, sin
    coerciones raras estilo JS ([] == false, etc. -- no hace falta
    replicar TODA la tabla de coerción de JS, solo lo que un protocolo
    binario real necesita).
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
            return object()  # no matchea nada, evita falsos positivos


def eval_condition(expr: str, fields: dict[str, Any],
                    root: dict[str, Any] | None = None,
                    parent: dict[str, Any] | None = None) -> bool:
    """
    Evalúa una expresión de condición tipo protodef contra el dict `fields`
    del container actual. `root` y `parent` son opcionales, para condiciones
    que referencian `$root.algo` o `$parent.algo`.
    """
    context = {"fields": fields, "$root": root or {}, "$parent": parent or {}}
    try:
        tokens = _tokenize(expr)
        parser = _Parser(tokens, context)
        result = parser.parse_expr()
        if parser.pos != len(tokens):
            raise ValueError(f"tokens sobrantes al final de la expresión: {expr!r}")
    except ValueError as exc:
        # Bug real: el tokenizer/parser de más arriba lanzaban ValueError
        # crudo, pero errors.py define (y el README documenta, sección 12)
        # ConditionError como la excepción propia de este módulo -- nadie
        # que hiciera `except ConditionError` la estaba atrapando nunca.
        raise ConditionError(expr, str(exc)) from exc
    return bool(result)
