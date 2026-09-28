"""Desktop notifications (Epic L2). plyer is the de facto cross-platform
wrapper for this -- it shells out to osascript on macOS, notify-send on
Linux, and uses the native Windows toast API, so this module doesn't have
to special-case three different platforms itself.

Send-only, never reads anything back, so unlike read_clipboard there's no
privacy angle here -- just a plain "do this" action, the same risk tier
as creating a calendar event that isn't flagged important. Not
confirmation-gated.
"""
from __future__ import annotations

from typing import Any

from plyer import notification

from core.exceptions import ToolExecutionError
from tools.base import ToolSpec
from tools.registry import ToolRegistry


def register_notification_tools(registry: ToolRegistry) -> None:
    async def send_notification(args: dict[str, Any]) -> str:
        title = args["title"]
        message = args["message"]
        try:
            notification.notify(title=title, message=message, app_name="AuraAgent", timeout=10)
        except Exception as exc:  # noqa: BLE001 - plyer's backends raise a variety of platform-specific errors
            raise ToolExecutionError(f"Could not send a desktop notification: {exc}") from exc
        return f"Sent notification: '{title}'."

    registry.register(
        ToolSpec(
            name="send_notification",
            description="Show a desktop notification (a system toast/banner) with a title and message.",
            input_schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Notification title."},
                    "message": {"type": "string", "description": "Notification body text."},
                },
                "required": ["title", "message"],
            },
        ),
        send_notification,
    )
