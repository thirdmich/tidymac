"""Tool specifications and execution engine for TidyCopilot."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..cleaner import (
    is_safe_to_remove,
    remove_files,
    run_cleanup_command,
)
from ..patterns import (
    contains_protected_path,
    fold_path_case,
    is_allowed_cleanup_command,
    is_system_protected,
    is_user_data,
)
from ..scanner import (
    format_size,
    get_disk_usage,
    scan_clutter,
    scan_duplicates,
    scan_large_files,
    _find_dependency_dirs,
    _is_project_stale,
    _dir_size,
)


def is_path_excluded(path: Path | str) -> bool:
    """Check if a path falls under user-configured excluded directories (e.g. ~/projects)."""
    try:
        from .config import load_ai_config
        cfg = load_ai_config()
        p_str = fold_path_case(Path(path).expanduser().resolve())
        for exc in cfg.excluded_paths:
            exc_str = fold_path_case(Path(exc).expanduser().resolve())
            inside = p_str == exc_str or p_str.startswith(exc_str + os.sep)
            # A parent of an excluded folder would delete the excluded folder with it
            contains = exc_str.startswith(p_str.rstrip(os.sep) + os.sep)
            if inside or contains:
                return True
    except Exception:
        # Fail closed: if the exclusion list can't be checked, treat the path as protected.
        return True
    return False


@dataclass
class PlanItem:

    path: str
    size_bytes: int
    category: str
    reason: str
    is_safe: bool = True
    cleanup_command: str | None = None
    is_protected: bool = False


@dataclass
class CleanupPlan:
    title: str
    items: list[PlanItem] = field(default_factory=list)
    total_bytes: int = 0
    safe_bytes: int = 0
    caution_bytes: int = 0
    blocked_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "total_bytes": self.total_bytes,
            "total_human": format_size(self.total_bytes),
            "item_count": len(self.items),
            "blocked_count": self.blocked_count,
            "items": [
                {
                    "path": it.path,
                    "size_human": format_size(it.size_bytes),
                    "size_bytes": it.size_bytes,
                    "category": it.category,
                    "reason": it.reason,
                    "is_safe": it.is_safe,
                    "cleanup_command": it.cleanup_command,
                    "is_protected": it.is_protected,
                }
                for it in self.items
            ],
        }


# --- Tool Implementations ---


def tool_get_disk_overview() -> dict[str, Any]:
    """Get overall filesystem storage usage statistics."""
    total, used, free = get_disk_usage()
    pct = (used / total * 100) if total > 0 else 0
    return {
        "total_bytes": total,
        "used_bytes": used,
        "free_bytes": free,
        "total_human": format_size(total),
        "used_human": format_size(used),
        "free_human": format_size(free),
        "used_percent": round(pct, 1),
    }


def tool_scan_clutter(include_details: bool = False) -> dict[str, Any]:
    """Scan standard system caches, dev stores, logs, and temp directories."""
    results = scan_clutter()
    categories = []
    total_bytes = 0

    for r in results:
        total_bytes += r.total_size
        cat_info: dict[str, Any] = {
            "name": r.category.name,
            "description": r.category.description,
            "size_bytes": r.total_size,
            "size_human": format_size(r.total_size),
            "file_count": r.file_count,
            "safe": r.category.safe,
            "cleanup_command": r.category.cleanup_command,
        }
        if include_details:
            cat_info["sample_paths"] = [str(p) for p in r.paths[:10]]
        categories.append(cat_info)

    return {
        "total_clutter_bytes": total_bytes,
        "total_clutter_human": format_size(total_bytes),
        "category_count": len(categories),
        "categories": categories,
    }


def tool_scan_large_files(
    min_size_mb: int = 100,
    search_path: str | None = None,
    limit: int = 25,
) -> dict[str, Any]:
    """Find large files taking up disk space."""
    root = Path(search_path).expanduser() if search_path else Path.home()
    if not root.exists():
        return {"error": f"Path does not exist: {search_path}"}

    threshold_bytes = max(1, min_size_mb) * 1024 * 1024
    files = scan_large_files(root, min_size=threshold_bytes, include_user_data=True)
    files = [f for f in files if not is_path_excluded(f.path)]

    items = []
    total_found = 0
    for f in files[:limit]:
        total_found += f.size
        items.append({
            "path": str(f.path),
            "size_bytes": f.size,
            "size_human": f.size_human(),
            "modified_days_ago": f.modified_days_ago,
            "is_user_data": is_user_data(f.path),
        })

    return {
        "searched_path": str(root),
        "threshold_human": format_size(threshold_bytes),
        "count_found": len(files),
        "showing": len(items),
        "total_size_human": format_size(total_found),
        "files": items,
    }


def tool_scan_duplicates(
    search_path: str | None = None,
    min_size_mb: int = 5,
    limit: int = 15,
) -> dict[str, Any]:
    """Find duplicate files using content hashing."""
    root = Path(search_path).expanduser() if search_path else Path.home() / "Downloads"
    if not root.exists():
        return {"error": f"Path does not exist: {search_path}"}

    threshold_bytes = max(1, min_size_mb) * 1024 * 1024
    groups = scan_duplicates(root, min_size=threshold_bytes)
    groups = [g for g in groups if not any(is_path_excluded(p) for p in g.files)]

    items = []
    total_wasted = 0
    for g in groups[:limit]:
        total_wasted += g.wasted_space
        items.append({
            "hash": g.file_hash[:12],
            "file_size_human": format_size(g.size),
            "copies_count": len(g.files),
            "wasted_bytes": g.wasted_space,
            "wasted_human": format_size(g.wasted_space),
            "files": [str(p) for p in g.files],
        })

    return {
        "searched_path": str(root),
        "duplicate_groups_count": len(groups),
        "total_wasted_human": format_size(total_wasted),
        "groups": items,
    }


def tool_scan_stale_projects(
    search_path: str | None = None,
    stale_days: int = 30,
    limit: int = 20,
) -> dict[str, Any]:
    """Find stale developer project directories (node_modules, venvs) untouched for N days."""
    root = Path(search_path).expanduser() if search_path else Path.home()
    if not root.exists():
        return {"error": f"Path does not exist: {search_path}"}

    if is_path_excluded(root):
        return {
            "searched_path": str(root),
            "stale_days_cutoff": stale_days,
            "count": 0,
            "total_reclaimable_human": "0 B",
            "projects": [],
            "note": "Workspace is strictly protected per user instruction.",
        }

    dep_dirs = _find_dependency_dirs(root, "node_modules", max_depth=5)
    dep_dirs = [d for d in dep_dirs if not is_path_excluded(d)]
    stale_items = []
    total_size = 0


    for d in dep_dirs:
        if _is_project_stale(d, stale_days=stale_days):
            sz = _dir_size(d)
            total_size += sz
            stale_items.append({
                "project_path": str(d.parent),
                "dependency_dir": str(d),
                "size_bytes": sz,
                "size_human": format_size(sz),
                "type": "node_modules",
                "safe_to_rebuild": True,
            })
            if len(stale_items) >= limit:
                break

    return {
        "searched_path": str(root),
        "stale_days_cutoff": stale_days,
        "count": len(stale_items),
        "total_reclaimable_human": format_size(total_size),
        "projects": stale_items,
    }


def tool_scan_media(
    search_path: str | None = None,
    min_video_size_mb: int = 25,
    find_duplicates: bool = True,
    limit: int = 15,
) -> dict[str, Any]:
    """Scan for photos, videos, screen recordings, duplicates, and Apple Photos libraries."""
    from ..scanner import scan_media

    root = Path(search_path).expanduser() if search_path else None
    res = scan_media(roots=root, min_video_size_mb=min_video_size_mb, find_duplicates=find_duplicates)

    libraries = []
    for lib in res.photo_libraries:
        libraries.append({
            "name": lib.name,
            "path": str(lib.path),
            "size_bytes": lib.size,
            "size_human": lib.size_human(),
            "is_apple_photos": lib.is_apple_photos,
            "safety_note": (
                "PROTECTED BUNDLE: Do NOT delete files directly inside this library. "
                "Use Apple Photos app to review 'Recently Deleted', 'Duplicates', or turn on 'Optimize Mac Storage'."
                if lib.is_apple_photos
                else "Application project library (managed by editing app)."
            ),
        })

    videos = [
        {"path": str(v.path), "size_bytes": v.size, "size_human": v.size_human(), "modified_days_ago": v.modified_days_ago}
        for v in res.large_videos if not is_path_excluded(v.path)
    ][:limit]

    screen_recs = [
        {"path": str(v.path), "size_bytes": v.size, "size_human": v.size_human(), "modified_days_ago": v.modified_days_ago}
        for v in res.screen_recordings if not is_path_excluded(v.path)
    ][:limit]

    screenshots = [
        {"path": str(s.path), "size_bytes": s.size, "size_human": s.size_human(), "modified_days_ago": s.modified_days_ago}
        for s in res.screenshots if not is_path_excluded(s.path)
    ][:limit]

    dupe_groups = []
    visible_groups = [g for g in res.duplicate_groups if not any(is_path_excluded(p) for p in g.files)]
    for g in visible_groups[:limit]:
        dupe_groups.append({
            "hash": g.file_hash[:8],
            "file_size_human": format_size(g.size),
            "copies_count": len(g.files),
            "wasted_bytes": g.wasted_space,
            "wasted_human": format_size(g.wasted_space),
            "files": [str(p) for p in g.files],
        })

    return {
        "photo_libraries": libraries,
        "large_videos": videos,
        "screen_recordings": screen_recs,
        "screenshots": screenshots,
        "duplicate_groups": dupe_groups,
        "total_standalone_videos_human": format_size(res.total_video_bytes),
        "total_standalone_photos_human": format_size(res.total_photo_bytes),
    }



def tool_propose_cleanup_plan(
    title: str,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Formulate and validate a cleanup plan. Validates every item against Tier 1/2 safety policies."""
    plan = CleanupPlan(title=title)

    for raw in items:
        p_str = raw.get("path", "")
        if not p_str and not raw.get("cleanup_command"):
            continue

        size = int(raw.get("size_bytes", 0))
        cat = raw.get("category", "General")
        reason = raw.get("reason", "Identified by AI analysis")
        cmd = raw.get("cleanup_command")

        p = Path(p_str).expanduser().resolve() if p_str else None

        # Check safety guardrails
        is_prot = False
        safe = True

        if cmd:
            if is_allowed_cleanup_command(cmd):
                plan.safe_bytes += size
            else:
                is_prot = True
                safe = False
                plan.blocked_count += 1
                reason = "BLOCKED: Command is not one of tidymac's known package-manager cleanup commands."
        elif p:
            if is_system_protected(p) or contains_protected_path(p) or is_path_excluded(p):
                is_prot = True
                safe = False
                plan.blocked_count += 1
                reason = "BLOCKED: Path is protected by system safety or user workspace exclusion policy."

            elif not is_safe_to_remove(p):
                safe = False
                plan.caution_bytes += size
            else:
                plan.safe_bytes += size


        plan.total_bytes += size
        plan.items.append(
            PlanItem(
                path=p_str,
                size_bytes=size,
                category=cat,
                reason=reason,
                is_safe=safe,
                cleanup_command=cmd,
                is_protected=is_prot,
            )
        )

    return plan.to_dict()


# --- Registry and JSON Schemas ---

TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "get_disk_overview",
            "description": "Fetch overall filesystem disk usage (total, used, free space).",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scan_clutter",
            "description": "Scan standard system caches, package manager caches, and temp files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "include_details": {
                        "type": "boolean",
                        "description": "Include sample paths of files in each category.",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scan_large_files",
            "description": "Find large files above a specified size threshold.",
            "parameters": {
                "type": "object",
                "properties": {
                    "min_size_mb": {
                        "type": "integer",
                        "description": "Minimum file size in megabytes (default 100).",
                    },
                    "search_path": {
                        "type": "string",
                        "description": "Directory path to search in (defaults to user home).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of files to return (default 25).",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scan_duplicates",
            "description": "Find duplicate files in a directory using hash verification.",
            "parameters": {
                "type": "object",
                "properties": {
                    "search_path": {
                        "type": "string",
                        "description": "Directory to scan (defaults to ~/Downloads).",
                    },
                    "min_size_mb": {
                        "type": "integer",
                        "description": "Minimum file size in MB to inspect (default 5).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of duplicate groups to return (default 15).",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scan_stale_projects",
            "description": "Find stale developer project dependencies (e.g. node_modules) untouched for 30+ days.",
            "parameters": {
                "type": "object",
                "properties": {
                    "search_path": {
                        "type": "string",
                        "description": "Root path to scan for code repositories (defaults to home).",
                    },
                    "stale_days": {
                        "type": "integer",
                        "description": "Cutoff days since last modification (default 30).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of projects to return (default 20).",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scan_media",
            "description": "Scan for photos, videos, screen recordings, duplicates, and Apple Photos libraries.",
            "parameters": {
                "type": "object",
                "properties": {
                    "search_path": {
                        "type": "string",
                        "description": "Specific directory to search (defaults to Movies, Pictures, Downloads, Desktop).",
                    },
                    "min_video_size_mb": {
                        "type": "integer",
                        "description": "Threshold for large video files in MB (default 25).",
                    },
                    "find_duplicates": {
                        "type": "boolean",
                        "description": "Whether to check for duplicate photo/video files (default True).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum items to return per media category (default 15).",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_cleanup_plan",
            "description": "Formulate a concrete, validated cleanup plan. Call this before presenting options to the user.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Descriptive title for this cleanup plan.",
                    },
                    "items": {
                        "type": "array",
                        "description": "List of files, directories, or categories to clean.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string", "description": "Absolute path to file or folder."},
                                "size_bytes": {"type": "integer", "description": "Estimated bytes freed."},
                                "category": {"type": "string", "description": "Category name (e.g. Cache, Logs, Large File)."},
                                "reason": {"type": "string", "description": "Why this item should be cleaned."},
                                "cleanup_command": {"type": "string", "description": "CLI command if cleaned via package manager."},
                            },
                            "required": ["category", "reason"],
                        },
                    },
                },
                "required": ["title", "items"],
            },
        },
    },
]


TOOL_FUNCTIONS: dict[str, Callable[..., Any]] = {
    "get_disk_overview": tool_get_disk_overview,
    "scan_clutter": tool_scan_clutter,
    "scan_large_files": tool_scan_large_files,
    "scan_duplicates": tool_scan_duplicates,
    "scan_stale_projects": tool_scan_stale_projects,
    "scan_media": tool_scan_media,
    "propose_cleanup_plan": tool_propose_cleanup_plan,
}



def execute_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Execute a tool function by name with parsed arguments."""
    fn = TOOL_FUNCTIONS.get(name)
    if not fn:
        return {"error": f"Unknown tool: '{name}'"}
    try:
        return fn(**arguments)
    except Exception as exc:
        return {"error": f"Tool execution failed for '{name}': {exc}"}
