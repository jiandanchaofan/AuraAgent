"""calculate — safe math expression evaluation tool.

Delegates to tools/calc/safe_math.py's hand-written AST interpreter, never
Python's eval()/exec(), so there is no code-execution escape hatch for the
model to find regardless of what expression string it passes.
"""
from __future__ import annotations

from typing import Any

from core.exceptions import ToolExecutionError
from tools.base import ToolSpec
from tools.calc import safe_math
from tools.registry import ToolRegistry


def register_calculate_tools(registry: ToolRegistry) -> None:
    async def calculate(args: dict[str, Any]) -> str:
        expression = args["expression"]
        try:
            result = safe_math.evaluate(expression)
        except safe_math.UnsafeExpressionError as exc:
            raise ToolExecutionError(f"Cannot evaluate expression: {exc}") from exc
        except ZeroDivisionError as exc:
            raise ToolExecutionError(f"Division by zero in expression '{expression}'.") from exc
        except OverflowError as exc:
            raise ToolExecutionError(f"Result too large to compute for expression '{expression}'.") from exc
        return str(result)

    registry.register(
        ToolSpec(
            name="calculate",
            description=(
                "Evaluate a numeric math expression safely. Supports + - * / ** % () "
                "and the functions sqrt, abs, round, min, max. Does NOT support "
                "variables, strings, comparisons, imports, or arbitrary code."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "A math expression, e.g. '2 * (3 + 4) ** 2' or 'sqrt(16)'",
                    }
                },
                "required": ["expression"],
            },
        ),
        calculate,
    )
