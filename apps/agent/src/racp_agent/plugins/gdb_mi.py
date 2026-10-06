"""Bounded GDB/MI records. This is not a CLI-expression or backend-command parser."""

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MIRecord:
    token: str | None
    kind: str
    name: str
    data: dict[str, Any]


class Parser:
    def __init__(self, source: bytes) -> None:
        if len(source) > 1024 * 1024:
            raise ValueError("GDB/MI line exceeds limit")
        self.source, self.position = source.rstrip(b"\r\n"), 0

    def char(self) -> int:
        return self.source[self.position] if self.position < len(self.source) else 0

    def identifier(self) -> str:
        match = re.match(rb"[A-Za-z0-9_-]+", self.source[self.position :])
        if match is None:
            raise ValueError("invalid MI identifier")
        self.position += len(match[0])
        return match[0].decode("ascii")

    def string(self) -> str:
        if self.char() != 34:
            raise ValueError("MI string must be quoted")
        self.position += 1
        value = bytearray()
        escapes = {
            ord("n"): 10,
            ord("r"): 13,
            ord("t"): 9,
            ord("b"): 8,
            ord("f"): 12,
            ord("v"): 11,
            92: 92,
            34: 34,
        }
        while self.char():
            char = self.char()
            self.position += 1
            if char == 34:
                if len(value) > 256 * 1024:
                    raise ValueError("MI string exceeds limit")
                return value.decode("utf-8", errors="replace")
            if char == 92:
                char = self.char()
                self.position += 1
                if 48 <= char <= 55:
                    digits = bytes([char])
                    for _ in range(2):
                        if 48 <= self.char() <= 55:
                            digits += bytes([self.char()])
                            self.position += 1
                    value.append(int(digits, 8) & 255)
                elif char in escapes:
                    value.append(escapes[char])
                else:
                    raise ValueError("unsupported MI escape")
            else:
                value.append(char)
        raise ValueError("unterminated MI string")

    def result(self, depth: int) -> tuple[str, Any]:
        key = self.identifier()
        if self.char() != 61:
            raise ValueError("MI result requires equals")
        self.position += 1
        return key, self.value(depth + 1)

    def value(self, depth: int = 0) -> Any:
        if depth > 32:
            raise ValueError("MI nesting exceeds limit")
        if self.char() == 34:
            return self.string()
        opening = self.char()
        if opening not in {91, 123}:
            raise ValueError("invalid MI value")
        self.position += 1
        ending = 93 if opening == 91 else 125
        values: list[Any] = []
        mapping: dict[str, Any] = {}
        while self.char() != ending:
            if opening == 123 or self.char() not in {34, 91, 123}:
                key, value = self.result(depth)
                if opening == 123:
                    if key in mapping:
                        raise ValueError("duplicate MI tuple field")
                    mapping[key] = value
                else:
                    values.append({key: value})
            else:
                values.append(self.value(depth + 1))
            if self.char() == 44:
                self.position += 1
            elif self.char() != ending:
                raise ValueError("MI container missing separator")
        self.position += 1
        return values if opening == 91 else mapping

    def record(self) -> MIRecord:
        token_match = re.match(rb"[0-9]{1,20}", self.source)
        token = token_match[0].decode() if token_match else None
        if token_match:
            self.position = len(token_match[0])
        kind = chr(self.char())
        self.position += 1
        if kind in "~@&":
            name, data = "stream", {"text": self.string()}
        elif kind in "^*+=":
            name, data = self.identifier(), {}
            while self.char() == 44:
                self.position += 1
                key, value = self.result(0)
                if key in data:
                    raise ValueError("duplicate MI result field")
                data[key] = value
        else:
            raise ValueError("not an MI record")
        if self.position != len(self.source):
            raise ValueError("MI record has trailing data")
        return MIRecord(token, kind, name, data)


def parse_record(raw: bytes) -> MIRecord:
    return Parser(raw).record()
