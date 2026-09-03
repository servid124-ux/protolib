"""
protolib/protobuf/proto_lexer.py

Tokenizer for .proto source text, following the lexical grammar in
https://protobuf.dev/reference/protobuf/proto3-spec/ closely enough to
cover real-world .proto files (identifiers, integer/float/string
literals, punctuation, and both comment styles) without pulling in a
full grammar/parser-generator dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from .errors import ProtoSyntaxError


class TokenType(Enum):
    IDENT = auto()      # message, Foo, my_field, int32, ...
    INT = auto()        # 1, 0x1A, 0o17, 019 (octal per spec: leading 0)
    FLOAT = auto()       # 1.5, 1e10, .5, inf, nan
    STRING = auto()      # "hello" or 'hello'
    SYMBOL = auto()      # { } ( ) [ ] < > = ; , . -
    EOF = auto()


@dataclass
class Token:
    type: TokenType
    value: object  # str for IDENT/SYMBOL, int for INT, float for FLOAT, str for STRING
    line: int
    column: int

    def __repr__(self) -> str:
        return f"Token({self.type.name}, {self.value!r}, {self.line}:{self.column})"


_SYMBOLS = "{}()[]<>=;,.-+:"

_KEYWORDS_NOT_SPECIAL = frozenset()  # keywords are just identifiers; the parser decides meaning


def tokenize(source: str) -> list[Token]:
    """Tokenizes .proto source text into a flat list of Tokens, ending
    with a single EOF token. Raises ProtoSyntaxError on malformed
    literals (unterminated strings, invalid escape sequences, etc.)."""
    tokens: list[Token] = []
    i = 0
    n = len(source)
    line = 1
    col = 1

    def advance(count: int = 1) -> None:
        nonlocal i, line, col
        for _ in range(count):
            if i < n and source[i] == "\n":
                line += 1
                col = 1
            else:
                col += 1
            i += 1

    while i < n:
        ch = source[i]

        # Whitespace
        if ch in " \t\r\n":
            advance()
            continue

        # Line comment
        if ch == "/" and i + 1 < n and source[i + 1] == "/":
            while i < n and source[i] != "\n":
                advance()
            continue

        # Block comment
        if ch == "/" and i + 1 < n and source[i + 1] == "*":
            start_line, start_col = line, col
            advance(2)
            closed = False
            while i < n:
                if source[i] == "*" and i + 1 < n and source[i + 1] == "/":
                    advance(2)
                    closed = True
                    break
                advance()
            if not closed:
                raise ProtoSyntaxError("unterminated block comment", start_line, start_col)
            continue

        start_line, start_col = line, col

        # String literal (single or double quoted, per spec supports
        # both, with C-style backslash escapes)
        if ch in "\"'":
            quote = ch
            advance()
            chars: list[str] = []
            closed = False
            while i < n:
                c = source[i]
                if c == quote:
                    advance()
                    closed = True
                    break
                if c == "\n":
                    break  # spec: strings can't span lines unescaped
                if c == "\\" and i + 1 < n:
                    nxt = source[i + 1]
                    escapes = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\",
                               "'": "'", '"': '"', "0": "\0"}
                    if nxt in escapes:
                        chars.append(escapes[nxt])
                        advance(2)
                        continue
                    else:
                        chars.append(nxt)
                        advance(2)
                        continue
                chars.append(c)
                advance()
            if not closed:
                raise ProtoSyntaxError("unterminated string literal", start_line, start_col)
            tokens.append(Token(TokenType.STRING, "".join(chars), start_line, start_col))
            continue

        # Number literal: int or float. Per spec, floats can start with
        # a digit or '.', and hex/octal ints are supported (0x1A, 017).
        if ch.isdigit() or (ch == "." and i + 1 < n and source[i + 1].isdigit()):
            j = i
            is_float = False
            if source[j] == "0" and j + 1 < n and source[j + 1] in "xX":
                j += 2
                while j < n and source[j] in "0123456789abcdefABCDEF":
                    j += 1
                text = source[i:j]
                advance(j - i)
                tokens.append(Token(TokenType.INT, int(text, 16), start_line, start_col))
                continue
            while j < n and source[j].isdigit():
                j += 1
            if j < n and source[j] == ".":
                is_float = True
                j += 1
                while j < n and source[j].isdigit():
                    j += 1
            if j < n and source[j] in "eE":
                is_float = True
                k = j + 1
                if k < n and source[k] in "+-":
                    k += 1
                if k < n and source[k].isdigit():
                    j = k
                    while j < n and source[j].isdigit():
                        j += 1
            text = source[i:j]
            advance(j - i)
            if is_float:
                tokens.append(Token(TokenType.FLOAT, float(text), start_line, start_col))
            else:
                # A leading-zero integer (other than "0" itself) is
                # octal per the proto3 spec's lexical grammar.
                if len(text) > 1 and text[0] == "0":
                    tokens.append(Token(TokenType.INT, int(text, 8), start_line, start_col))
                else:
                    tokens.append(Token(TokenType.INT, int(text), start_line, start_col))
            continue

        # Identifier / keyword (letters, digits, underscore; must not
        # start with a digit -- that case is already handled above)
        if ch.isalpha() or ch == "_":
            j = i
            while j < n and (source[j].isalnum() or source[j] == "_"):
                j += 1
            text = source[i:j]
            advance(j - i)
            tokens.append(Token(TokenType.IDENT, text, start_line, start_col))
            continue

        # Symbols/punctuation
        if ch in _SYMBOLS:
            advance()
            tokens.append(Token(TokenType.SYMBOL, ch, start_line, start_col))
            continue

        raise ProtoSyntaxError(f"unexpected character {ch!r}", start_line, start_col)

    tokens.append(Token(TokenType.EOF, None, line, col))
    return tokens
