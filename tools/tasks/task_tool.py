"""TaskTool — todo/task management, wired the same way as
tools/calendar/calendar_tool.py: delete_task always asks for confirmation
via the SAME ConfirmationChannel instance already constructed for the
calendar tool (main.py passes it through — no second channel is built).
"""
from __future__ import annotations

from typing import Any

from confirmation.base import ConfirmationChannel, ConfirmationRequest
from core.exceptions import ToolExecutionError
from tools.base import ToolSpec
from tools.registry import ToolRegistry
from tools.tasks.task_provider import TaskNotFoundError, TaskProvider


def register_task_tools(
    registry: ToolRegistry,
    provider: TaskProvider,
    confirmation_channel: ConfirmationChannel,
) -> None:
    async def create_task(args: dict[str, Any]) -> str:
        task = await provider.create_task(args["title"], args.get("notes"))
        return f"Created task '{task.title}' (id={task.id})."

    async def list_tasks(args: dict[str, Any]) -> str:
        include_completed = args.get("include_completed", True)
        tasks = await provider.list_tasks(include_completed=include_completed)
        if not tasks:
            return "No tasks found."
        lines = [f"- [{t.id}] {t.title}" + (" [DONE]" if t.done else "") for t in tasks]
        return f"Found {len(tasks)} task(s):\n" + "\n".join(lines)

    async def complete_task(args: dict[str, Any]) -> str:
        try:
            task = await provider.complete_task(args["task_id"])
        except TaskNotFoundError as exc:
            raise ToolExecutionError(str(exc)) from exc
        return f"Completed task '{task.title}'."

    async def delete_task(args: dict[str, Any]) -> str:
        task_id = args["task_id"]
        try:
            task = await provider.get_task(task_id)
        except TaskNotFoundError as exc:
            raise ToolExecutionError(str(exc)) from exc

        approved = await confirmation_channel.confirm(
            ConfirmationRequest(
                tool_name="delete_task",
                arguments=args,
                reason=f"Delete task '{task.title}'? This cannot be undone.",
                risk_level="destructive",
            )
        )
        if not approved:
            return f"User declined to delete task '{task.title}'."

        await provider.delete_task(task_id)
        return f"Deleted task '{task.title}'."

    registry.register(
        ToolSpec(
            name="create_task",
            description="Create a new todo/task item.",
            input_schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "notes": {"type": "string", "description": "Optional extra detail"},
                },
                "required": ["title"],
            },
        ),
        create_task,
    )
    registry.register(
        ToolSpec(
            name="list_tasks",
            description="List tasks. Set include_completed=false to see only unfinished ones.",
            input_schema={
                "type": "object",
                "properties": {
                    "include_completed": {"type": "boolean", "description": "Defaults to true."}
                },
                "required": [],
            },
        ),
        list_tasks,
    )
    registry.register(
        ToolSpec(
            name="complete_task",
            description="Mark a task as completed.",
            input_schema={
                "type": "object",
                "properties": {"task_id": {"type": "string"}},
                "required": ["task_id"],
            },
        ),
        complete_task,
    )
    registry.register(
        ToolSpec(
            name="delete_task",
            description="Delete a task by id. Always asks the user to confirm before deleting.",
            input_schema={
                "type": "object",
                "properties": {"task_id": {"type": "string"}},
                "required": ["task_id"],
            },
        ),
        delete_task,
    )
