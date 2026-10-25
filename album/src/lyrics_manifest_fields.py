"""The manifest's field readers: untrusted JSON read one expected type at a time, or refused.

The manifest and frame codecs (`lyrics_manifest`) share them. Every refusal is a
`ManifestSchemaError` naming the field, never another exception, and a boolean is never
taken for a number.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from enum import StrEnum
from pathlib import PurePosixPath
from typing import TypeVar

from lyrics_model import ManifestSchemaError

_E = TypeVar("_E", bound=StrEnum)
_T = TypeVar("_T")


def malformed(where: str, expected: str) -> ManifestSchemaError:
    """The error for a value at *where* that is not *expected*."""
    return ManifestSchemaError(f"malformed manifest: {where} is not {expected}")


class Fields:
    """A JSON object's fields, each read as one expected type or refused.

    *where* names the object in an error message, so a refusal says which field of
    which entry was wrong.
    """

    def __init__(self, value: object, where: str) -> None:
        """Hold *value*, refused unless it is a JSON object."""
        if not isinstance(value, dict):
            raise malformed(where, "an object")
        self._fields: dict[object, object] = value
        self._where = where

    def at(self, key: str) -> str:
        """The name of field *key* in an error message."""
        return f"{self._where}.{key}"

    def has(self, key: str) -> bool:
        """Whether field *key* is present, for a field older manifests lack."""
        return key in self._fields

    def value(self, key: str) -> object:
        """Field *key* as stored; a missing field is refused."""
        if key not in self._fields:
            raise malformed(self.at(key), "present")
        return self._fields[key]

    def text(self, key: str) -> str:
        """Field *key* as a string."""
        return text_value(self.value(key), self.at(key))

    def number(self, key: str) -> int:
        """Field *key* as an integer; a boolean is refused."""
        return number_value(self.value(key), self.at(key))

    def flag(self, key: str) -> bool:
        """Field *key* as a boolean."""
        value = self.value(key)
        if not isinstance(value, bool):
            raise malformed(self.at(key), "true or false")
        return value

    def optional(self, key: str, read: Callable[[object, str], _T]) -> _T | None:
        """Field *key* read by *read*, or None when it is null."""
        value = self.value(key)
        return None if value is None else read(value, self.at(key))

    def later(self, key: str, read: Callable[[object, str], _T]) -> _T | None:
        """`optional`, for a field older manifests lack: None when it is absent too."""
        return self.optional(key, read) if self.has(key) else None

    def each(self, key: str, read: Callable[[object, str], _T]) -> tuple[_T, ...]:
        """Field *key* as a list, each item read by *read*."""
        value = self.value(key)
        if not isinstance(value, list):
            raise malformed(self.at(key), "a list")
        return tuple(read(item, f"{self.at(key)}[{i}]") for i, item in enumerate(value))


def text_value(value: object, where: str) -> str:
    """*value* as a string, or refused."""
    if not isinstance(value, str):
        raise malformed(where, "a string")
    return value


def number_value(value: object, where: str) -> int:
    """*value* as an integer, or refused; JSON ``true`` is not a number."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise malformed(where, "an integer")
    return value


def real_value(value: object, where: str) -> float:
    """*value* as a finite number, a whole one included, or refused; ``true`` is not one."""
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise malformed(where, "a number")
    try:
        number = float(value)
    except OverflowError:  # a JSON integer too large for a float is not finite either
        raise malformed(where, "a finite number") from None
    if not math.isfinite(number):
        raise malformed(where, "a finite number")
    return number


def member(enum: type[_E]) -> Callable[[object, str], _E]:
    """A reader taking a value to a member of *enum*, or refusing it."""

    def read(value: object, where: str) -> _E:
        text = text_value(value, where)
        if text not in {member.value for member in enum}:
            raise malformed(where, f"a {enum.__name__}")
        return enum(text)

    return read


def relative_path(value: object, where: str) -> str:
    """*value* as a relative POSIX path that stays below its root, or refused."""
    text = text_value(value, where)
    path = PurePosixPath(text)
    climbs = path.is_absolute() or ".." in path.parts or not path.parts
    if climbs or str(path) != text or "\x00" in text:
        raise malformed(where, "a relative path inside its folder")
    return text


def file_name(value: object, where: str) -> str:
    """*value* as a plain file name: no folder, not ``.`` or ``..``, or refused."""
    text = relative_path(value, where)
    if len(PurePosixPath(text).parts) != 1:
        raise malformed(where, "a plain file name")
    return text
