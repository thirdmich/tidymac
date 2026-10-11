"""Rule-based heuristic provider for offline, zero-dependency natural language intent parsing."""

from __future__ import annotations

import json
import re
from typing import Any

from .base import BaseLLMProvider, ChatMessage, LLMResponse, ToolCall
from ..tools import execute_tool


class HeuristicProvider(BaseLLMProvider):
    """Offline intent engine that translates common cleanup queries into tool calls and plans."""

    name = "heuristic"

    def __init__(self) -> None:
        self._step = 0
        self._target_bytes: int | None = None
        self._intent: str = "general"

    def _parse_size_target(self, text: str) -> int | None:
        """Extract size targets like '10GB', '500MB', '2.5 GB' from query."""
        match = re.search(r"(\d+(?:\.\d+)?)\s*(GB|MB|TB|G|M|T)\b", text, re.IGNORECASE)
        if match:
            val = float(match.group(1))
            unit = match.group(2).upper()
            if unit in ("GB", "G"):
                return int(val * (1024**3))
            if unit in ("MB", "M"):
                return int(val * (1024**2))
            if unit in ("TB", "T"):
                return int(val * (1024**4))
        return None

    def chat(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """Parse conversation turn and return tool calls or synthetic response."""
        # Find latest user message
        user_msgs = [m for m in messages if m.role == "user"]
        latest_user = user_msgs[-1].content if user_msgs else ""
        lower_user = latest_user.lower()

        # Check if we already received tool responses in messages
        tool_results: list[ChatMessage] = [m for m in messages if m.role == "tool"]

        # Parse target size if present
        if self._target_bytes is None:
            self._target_bytes = self._parse_size_target(latest_user)

        # Detect intent
        if any(w in lower_user for w in ("photo", "video", "screenshot", "screen recording", "media", "movie", "picture", "mp4", "mov")):
            self._intent = "media"
        elif any(w in lower_user for w in ("duplicate", "dupes", "identical")):
            self._intent = "duplicates"
        elif any(w in lower_user for w in ("large", "big", "huge", "biggest")):
            self._intent = "large"
        elif any(w in lower_user for w in ("node_modules", "stale", "project", "dev")):
            self._intent = "stale_projects"
        elif any(w in lower_user for w in ("disk", "status", "usage", "free space", "overview", "storage")):
            if not any(w in lower_user for w in ("clean", "free", "delete", "clear", "remove")):
                self._intent = "status_only"
        else:
            self._intent = "clutter"

        # Step 1: No tool results yet -> invoke inspection tools
        if not tool_results:
            if self._intent == "media":
                return LLMResponse(
                    content="Scanning user media directories (Movies, Pictures, Downloads, Desktop) for videos, photos, screenshots, and libraries...",
                    tool_calls=[
                        ToolCall(id="call_media", name="scan_media", arguments={"min_video_size_mb": 25, "find_duplicates": True})
                    ],
                )
            if self._intent == "duplicates":
                return LLMResponse(
                    content="Scanning for duplicate files to locate wasted space...",
                    tool_calls=[
                        ToolCall(id="call_dupes", name="scan_duplicates", arguments={"min_size_mb": 5})
                    ],
                )

            if self._intent == "large":
                min_mb = 100
                if self._target_bytes:
                    min_mb = max(50, self._target_bytes // (1024 * 1024 * 10))
                return LLMResponse(
                    content="Scanning for large files across your home directory...",
                    tool_calls=[
                        ToolCall(id="call_large", name="scan_large_files", arguments={"min_size_mb": min_mb})
                    ],
                )
            if self._intent == "stale_projects":
                return LLMResponse(
                    content="Inspecting developer workspaces for stale dependencies (30+ days untouched)...",
                    tool_calls=[
                        ToolCall(id="call_stale", name="scan_stale_projects", arguments={"stale_days": 30})
                    ],
                )
            if self._intent == "status_only":
                return LLMResponse(
                    content="Checking filesystem storage statistics...",
                    tool_calls=[
                        ToolCall(id="call_disk", name="get_disk_overview", arguments={})
                    ],
                )

            # Default: scan clutter and check disk
            return LLMResponse(
                content="Analyzing system clutter, caches, and reclaimable storage...",
                tool_calls=[
                    ToolCall(id="call_clutter", name="scan_clutter", arguments={"include_details": True})
                ],
            )

        # Step 2: Tool results exist -> formulate a plan or respond
        # Check if a plan has already been proposed
        for m in tool_results:
            if m.name == "propose_cleanup_plan":
                try:
                    plan_data = json.loads(m.content)
                    total_h = plan_data.get("total_human", "0 B")
                    items_cnt = plan_data.get("item_count", 0)
                    return LLMResponse(
                        content=(
                            f"I've synthesized a cleanup plan containing **{items_cnt} items** "
                            f"that will reclaim **{total_h}** of disk space.\n\n"
                            f"Review the table above. If you'd like to proceed, confirm when prompted."
                        )
                    )
                except Exception:
                    pass

        # Check tool results to formulate `propose_cleanup_plan`
        plan_items: list[dict[str, Any]] = []

        for m in tool_results:
            try:
                data = json.loads(m.content)
            except Exception:
                continue

            # Disk status response
            if m.name == "get_disk_overview" and self._intent == "status_only":
                return LLMResponse(
                    content=(
                        f"📊 **Storage Overview**:\n"
                        f"- Total: {data.get('total_human')}\n"
                        f"- Used: {data.get('used_human')} ({data.get('used_percent')}%)\n"
                        f"- Free: {data.get('free_human')}\n"
                    )
                )

            # Media results
            if m.name == "scan_media":
                is_status = any(
                    w in lower_user
                    for w in ("what about", "how much", "status", "show", "check", "info", "overview")
                ) and not any(
                    w in lower_user for w in ("delete", "remove", "clean", "free", "purge")
                )

                libs = data.get("photo_libraries", [])
                apple_lib = next((l for l in libs if l.get("is_apple_photos")), None)
                other_libs = [l for l in libs if not l.get("is_apple_photos")]

                screen_recs = data.get("screen_recordings", [])
                screenshots = data.get("screenshots", [])
                large_videos = data.get("large_videos", [])
                dupe_groups = data.get("duplicate_groups", [])

                if is_status:
                    msg = "📸 **Photos & Videos Storage Overview**:\n\n"
                    if apple_lib:
                        msg += f"• **Apple Photos Library:** `{apple_lib['name']}` occupies **{apple_lib['size_human']}**\n"
                        msg += f"  > ℹ️ *Note: Apple Photos uses an internal SQLite database. Never delete files inside it directly. To free up space safely: open Photos app → empty 'Recently Deleted', review 'Duplicates', or turn on 'Optimize Mac Storage'.*\n\n"
                    if other_libs:
                        for ol in other_libs:
                            msg += f"• **Project Library:** `{ol['name']}` ({ol['size_human']})\n"
                    msg += f"• **Standalone Videos:** {data.get('total_standalone_videos_human', '0 B')} ({len(large_videos)} large files, {len(screen_recs)} screen recordings)\n"
                    msg += f"• **Standalone Photos & Screenshots:** {data.get('total_standalone_photos_human', '0 B')} ({len(screenshots)} screenshots)\n"
                    msg += f"• **Duplicate Media:** {len(dupe_groups)} duplicate groups found\n\n"
                    msg += "Would you like me to formulate a cleanup plan for **screen recordings**, **screenshots**, or **duplicate media**?"
                    return LLMResponse(content=msg)

                # Cleanup mode: propose safe candidate items
                for sr in screen_recs[:10]:
                    plan_items.append({
                        "category": "Screen Recording",
                        "size_bytes": sr["size_bytes"],
                        "path": sr["path"],
                        "reason": f"Screen recording occupying {sr['size_human']}",
                    })
                for g in dupe_groups[:10]:
                    for dup_path in g["files"][1:]:
                        plan_items.append({
                            "category": "Duplicate Media",
                            "size_bytes": g["wasted_bytes"] // (len(g["files"]) - 1),
                            "path": dup_path,
                            "reason": f"Duplicate copy of {g['files'][0]}",
                        })
                for sc in screenshots[:10]:
                    plan_items.append({
                        "category": "Screenshot",
                        "size_bytes": sc["size_bytes"],
                        "path": sc["path"],
                        "reason": f"Screenshot occupying {sc['size_human']}",
                    })
                for lv in large_videos[:5]:
                    plan_items.append({
                        "category": "Large Video",
                        "size_bytes": lv["size_bytes"],
                        "path": lv["path"],
                        "reason": f"Standalone video occupying {lv['size_human']}",
                    })

            # Clutter results
            if m.name == "scan_clutter":

                categories = data.get("categories", [])
                for cat in categories:
                    if cat.get("safe", True) and cat.get("size_bytes", 0) > 0:

                        cmd = cat.get("cleanup_command")
                        sample_path = ""
                        if not cmd and cat.get("sample_paths"):
                            sample_path = cat["sample_paths"][0]
                        plan_items.append({
                            "category": cat["name"],
                            "size_bytes": cat["size_bytes"],
                            "reason": cat.get("description", "System cache or temp store"),
                            "cleanup_command": cmd,
                            "path": sample_path,
                        })


            # Stale projects
            if m.name == "scan_stale_projects":
                projects = data.get("projects", [])
                for p in projects:
                    plan_items.append({
                        "category": "Stale node_modules",
                        "size_bytes": p["size_bytes"],
                        "path": p["dependency_dir"],
                        "reason": f"Project untouched for 30+ days; restorable via package manager",
                    })

            # Large files
            if m.name == "scan_large_files":
                files = data.get("files", [])
                for f in files[:10]:
                    plan_items.append({
                        "category": "Large File",
                        "size_bytes": f["size_bytes"],
                        "path": f["path"],
                        "reason": f"File occupies {f['size_human']}, last modified {f['modified_days_ago']} days ago",
                    })

            # Duplicates
            if m.name == "scan_duplicates":
                groups = data.get("groups", [])
                for g in groups:
                    # Suggest removing redundant copies, keeping the first
                    for dup_path in g["files"][1:]:
                        plan_items.append({
                            "category": "Duplicate Copy",
                            "size_bytes": g["wasted_bytes"] // (len(g["files"]) - 1),
                            "path": dup_path,
                            "reason": f"Duplicate of primary copy ({g['file_size_human']})",
                        })

        if plan_items:
            # If target bytes specified, truncate when target reached
            if self._target_bytes:
                accum = 0
                selected = []
                for item in plan_items:
                    selected.append(item)
                    accum += item.get("size_bytes", 0)
                    if accum >= self._target_bytes:
                        break
                plan_items = selected

            return LLMResponse(
                content="Synthesizing candidate cleanup plan...",
                tool_calls=[
                    ToolCall(
                        id="call_plan",
                        name="propose_cleanup_plan",
                        arguments={
                            "title": f"TidyCopilot Cleanup Plan ({self._intent.replace('_', ' ').title()})",
                            "items": plan_items,
                        },
                    )
                ],
            )

        return LLMResponse(
            content="I finished scanning your system. No significant clutter or waste matching your criteria was found!"
        )
