"""Screenshot capture (Epic L2). Saves a PNG into the workspace sandbox
and returns its path/size as plain text -- the model does NOT get to
"see" the image. Giving the model actual vision over a screenshot would
need ConversationTurn/ToolResultInput to carry image content blocks, with
matching translation code in both AnthropicProvider and OpenAIProvider
(the provider abstraction is text-only today) -- a distinct, larger
architecture change deliberately out of scope for this Epic.

ALWAYS confirmed, unlike every other tool in tools/system/: a screenshot
can capture anything currently on screen, not just something the user
pointed at -- other open windows, notifications, whatever's visible,
regardless of whether it has anything to do with this conversation. That
is a categorically different (and arguably larger) exposure than reading
a file the user named, so risk_level="privacy_exposure" (a new value, not
"destructive" -- see confirmation/base.py's ConfirmationRequest and
gui/frontend/src/lib/riskLevel.js's matching color entry) rather than
skipping confirmation the way read_clipboard does.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import mss
import mss.tools

from confirmation.base import ConfirmationChannel, ConfirmationRequest
from core.exceptions import ToolExecutionError
from tools.base import ToolSpec
from tools.registry import ToolRegistry
from tools.sandbox_path import resolve_within_sandbox
from tools.workspace_root import SwappableWorkspaceRoot


def register_screenshot_tools(
    registry: ToolRegistry, workspace_root: SwappableWorkspaceRoot, confirmation_channel: ConfirmationChannel
) -> None:
    async def take_screenshot(args: dict[str, Any]) -> str:
        filename = args.get("filename") or f"screenshot-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.png"
        relative_path = f"screenshots/{filename}" if "/" not in filename else filename
        # Read .current fresh here (not captured once at registration) so
        # /workspace set (cli/commands.py) takes effect on the very next
        # screenshot, without re-registering this tool.
        path = resolve_within_sandbox(workspace_root.current, relative_path)

        approved = await confirmation_channel.confirm(
            ConfirmationRequest(
                tool_name="take_screenshot",
                arguments=args,
                reason=(
                    "Take a screenshot of the current screen? This captures EVERYTHING currently visible "
                    "on screen -- other open windows, notifications, anything on display -- not just "
                    "something related to this conversation. The image is saved as a file; it is not sent "
                    "to the AI model."
                ),
                risk_level="privacy_exposure",
            )
        )
        if not approved:
            return "User declined to take a screenshot."

        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with mss.MSS() as sct:
                shot = sct.grab(sct.monitors[0])
            mss.tools.to_png(shot.rgb, shot.size, output=str(path))
        except mss.exception.ScreenShotError as exc:
            raise ToolExecutionError(f"Could not capture the screen: {exc}") from exc

        size_kb = path.stat().st_size / 1024
        return f"Saved screenshot to '{relative_path}' ({size_kb:.0f} KB, {shot.size[0]}x{shot.size[1]})."

    registry.register(
        ToolSpec(
            name="take_screenshot",
            description=(
                "Capture the current screen and save it as a PNG file in the workspace (default: "
                "screenshots/screenshot-<timestamp>.png). Always asks the user to confirm first, since it "
                "captures everything currently visible on screen. You cannot see the image's content "
                "yourself -- only report the saved path back to the user."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "Optional filename (or path within the workspace). Defaults to "
                        "screenshots/screenshot-<timestamp>.png.",
                    }
                },
            },
        ),
        take_screenshot,
    )
