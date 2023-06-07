from __future__ import annotations

import re
from collections.abc import Collection
from typing import Callable, Union, ClassVar

import ibis.expr.types as ir
import ibis.expr.datatypes as dt

from ibisml.core import Metadata


class Selector:
    """The base selector class"""

    __slots__ = ()
    _fields: ClassVar[tuple[str, ...]] = ()

    def __init_subclass__(cls):
        slots = []
        for base in cls.__mro__:
            if base is not object:
                slots.extend(reversed(base.__slots__))
        cls._fields = tuple(reversed(slots))

    def __repr__(self):
        name = type(self).__name__
        args = ",".join(repr(getattr(self, n)) for n in self._fields)
        return f"{name}({args})"

    def __eq__(self, other):
        return type(self) == type(other) and all(
            getattr(self, name) == getattr(other, name) for name in self._fields
        )

    def __and__(self, other: SelectionType) -> Selector:
        selectors = []
        for part in [self, selector(other)]:
            if isinstance(part, and_):
                selectors.extend(part.selectors)
            else:
                selectors.append(part)
        return and_(*selectors)

    def __or__(self, other: SelectionType) -> Selector:
        selectors = []
        for part in [self, selector(other)]:
            if isinstance(part, or_):
                selectors.extend(part.selectors)
            else:
                selectors.append(part)
        return or_(*selectors)

    def __sub__(self, other: SelectionType) -> Selector:
        return self & ~selector(other)

    def __invert__(self) -> Selector:
        if isinstance(self, not_):
            return self.selector
        return not_(self)

    def matches(self, col: ir.Column, metadata: Metadata) -> bool:
        """Whether the selector matches a given column"""
        raise NotImplementedError

    def select_columns(self, table: ir.Table, metadata: Metadata) -> list[str]:
        """Return a list of column names matching this selector."""
        return [
            c
            for c in table.columns
            if c not in metadata.outcomes
            and self.matches(table[c], metadata)  # type: ignore
        ]


SelectionType = Union[str, Collection[str], Callable[[ir.Column], bool], Selector]


def selector(obj: SelectionType) -> Selector:
    """Convert `obj` to a Selector"""
    if isinstance(obj, Selector):
        return obj
    elif isinstance(obj, str):
        return cols(obj)
    elif isinstance(obj, Collection):
        return cols(*obj)
    elif callable(obj):
        return where(obj)
    raise TypeError("Expected a str, list of strings, callable, or Selector")


class and_(Selector):
    __slots__ = ("selectors",)

    def __init__(self, *selectors):
        self.selectors = selectors

    def __repr__(self):
        args = " & ".join(repr(s) for s in self.selectors)
        return f"({args})"

    def matches(self, col: ir.Column, metadata: Metadata) -> bool:
        return all(s.matches(col, metadata) for s in self.selectors)


class or_(Selector):
    __slots__ = ("selectors",)

    def __init__(self, *selectors):
        self.selectors = selectors

    def __repr__(self):
        args = " | ".join(repr(s) for s in self.selectors)
        return f"({args})"

    def matches(self, col: ir.Column, metadata: Metadata) -> bool:
        return any(s.matches(col, metadata) for s in self.selectors)


class not_(Selector):
    __slots__ = ("selector",)

    def __init__(self, selector):
        self.selector = selector

    def __repr__(self):
        return f"~{self.selector!r}"

    def matches(self, col: ir.Column, metadata: Metadata) -> bool:
        return not self.selector.matches(col, metadata)


class everything(Selector):
    __slots__ = ()

    def matches(self, col: ir.Column, metadata: Metadata) -> bool:
        return True


class cols(Selector):
    __slots__ = ("columns",)

    def __init__(self, *columns: str):
        self.columns = tuple(columns)

    def matches(self, col: ir.Column, metadata: Metadata) -> bool:
        return col.get_name() in self.columns


class _StrMatcher(Selector):
    __slots__ = ("pattern",)

    def __init__(self, pattern: str):
        self.pattern = pattern


class contains(_StrMatcher):
    __slots__ = ()

    def matches(self, col: ir.Column, metadata: Metadata) -> bool:
        return self.pattern in col.get_name()


class endswith(_StrMatcher):
    __slots__ = ()

    def matches(self, col: ir.Column, metadata: Metadata) -> bool:
        return col.get_name().endswith(self.pattern)


class startswith(_StrMatcher):
    __slots__ = ()

    def matches(self, col: ir.Column, metadata: Metadata) -> bool:
        return col.get_name().startswith(self.pattern)


class matches(_StrMatcher):
    __slots__ = ()

    def matches(self, col: ir.Column, metadata: Metadata) -> bool:
        return re.search(self.pattern, col.get_name()) is not None


class has_type(Selector):
    __slots__ = ("dtype",)

    dtype: dt.DataType | type[dt.DataType]

    def __init__(self, dtype: dt.DataType | str | type[dt.DataType]):
        if isinstance(dtype, type):
            self.dtype = dtype
        else:
            self.dtype = dt.dtype(dtype)

    def matches(self, col: ir.Column, metadata: Metadata) -> bool:
        if metadata.get_categories(col.get_name()) is not None:
            return False
        if isinstance(self.dtype, type):
            return isinstance(col.type(), self.dtype)
        return col.type() == self.dtype


class _TypeSelector(Selector):
    __slots__ = ()
    _type: ClassVar[type]

    def matches(self, col: ir.Column, metadata: Metadata) -> bool:
        return metadata.get_categories(col.get_name()) is None and isinstance(
            col.type(), self._type
        )


class integer(_TypeSelector):
    __slots__ = ()
    _type = dt.Integer


class floating(_TypeSelector):
    __slots__ = ()
    _type = dt.Floating


class numeric(_TypeSelector):
    __slots__ = ()
    _type = dt.Numeric


class temporal(_TypeSelector):
    __slots__ = ()
    _type = dt.Temporal


class date(_TypeSelector):
    __slots__ = ()
    _type = dt.Date


class time(_TypeSelector):
    __slots__ = ()
    _type = dt.Time


class timestamp(_TypeSelector):
    __slots__ = ()
    _type = dt.Timestamp


class string(_TypeSelector):
    __slots__ = ()
    _type = dt.String


class nominal(Selector):
    __slots__ = ()

    def matches(self, col: ir.Column, metadata: Metadata) -> bool:
        return (
            isinstance(col.type(), dt.String)
            or metadata.get_categories(col.get_name()) is not None
        )


class categorical(Selector):
    __slots__ = ("ordered",)

    def __init__(self, ordered: bool | None = None):
        self.ordered = ordered

    def __repr__(self):
        if self.ordered is None:
            return "categorical()"
        return f"categorical(ordered={self.ordered})"

    def matches(self, col: ir.Column, metadata: Metadata) -> bool:
        categories = metadata.get_categories(col.get_name())
        if categories is None:
            return False
        if self.ordered is not None:
            return categories.ordered == self.ordered
        return True


class where(Selector):
    __slots__ = ("predicate",)

    def __init__(self, predicate: Callable[[ir.Column], bool]):
        self.predicate = predicate

    def matches(self, col: ir.Column, metadata: Metadata) -> bool:
        return self.predicate(col)
