"""Which calculation an analysis objective asks for."""

import re

UNSPECIFIED = "未说明计算类型"
AMBIGUOUS = "计算类型不唯一"
NO_VALUES = "未提供可计算的数值"
MANY_SERIES = "数值序列不唯一"
BAD_INPUT = "计算输入不符合要求"
MISSING_CALCULATION = "缺少成功的计算"

LABELS = {"growth_rate": "增长率", "difference": "差值", "mean": "均值", "sum": "求和"}

_REQUEST = re.compile(r"计算|均值|平均|增长率|差值|求和")
_OPERATIONS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("growth_rate", re.compile(r"增长率")),
    ("difference", re.compile(r"差值")),
    ("mean", re.compile(r"均值|平均")),
    ("sum", re.compile(r"求和")),
)


def requested_operation(objective: str) -> tuple[str | None, str | None]:
    """Return the one named operation, or a gap when calculation was asked for."""
    names = [name for name, pattern in _OPERATIONS if pattern.search(objective)]
    if len(names) == 1:
        return names[0], None
    if len(names) > 1:
        return None, AMBIGUOUS
    if _REQUEST.search(objective):
        return None, UNSPECIFIED
    return None, None


def series_ready(operation: str, values: list[float]) -> bool:
    if not values:
        return False
    if operation == "difference":
        return len(values) == 2
    if operation == "growth_rate":
        return values[0] != 0
    return True
