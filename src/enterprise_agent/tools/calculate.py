from typing import Literal


def calculate(operation: Literal["sum", "mean", "difference", "growth_rate"], values: list[float], denominator: float | None = None) -> float:
    if not values:
        raise ValueError("values must not be empty")
    if operation == "sum":
        return sum(values)
    if operation == "mean":
        return sum(values) / len(values)
    if operation == "difference":
        if len(values) != 2:
            raise ValueError("difference requires exactly two values")
        return values[0] - values[1]
    if operation == "growth_rate":
        base = denominator if denominator is not None else values[0]
        if base == 0:
            raise ValueError("growth_rate denominator must not be zero")
        current = values[-1]
        return (current - base) / base
    raise ValueError(f"unsupported calculation operation: {operation}")

