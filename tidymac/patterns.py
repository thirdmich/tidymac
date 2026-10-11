"""Clutter pattern definitions and protection rules for macOS and Windows cleanup."""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

HOME = Path.home()
IS_MACOS = sys.platform == "darwin"
IS_WINDOWS = sys.platform == "win32"

# Package-manager/build directories inside projects. Individual files in these
# are never safe to remove on their own — the whole directory is the unit, and
# it can be rebuilt (npm install, pod install, pip install, cargo build, ...).
DEPENDENCY_DIR_NAMES = {
    "node_modules",
    ".venv",
    "venv",
    ".git",
    "Pods",
    "vendor",
    "__pycache__",
    ".tox",
    ".next",
    ".nuxt",
    "target",
}

# Directories holding active app runtimes (VM root images, session data).
# Anything inside can corrupt a running or resumable VM if touched.
RUNTIME_DIR_NAMES = {"vm_bundles"}


@dataclass
class ClutterCategory:
    name: str
    description: str
    paths: list[str]  # Glob patterns or directory paths (used for sizing)
    safe: bool = True  # Safe to remove without side effects beyond needing to re-download/rebuild
    cleanup_command: str | None = None  # Clean via this CLI instead of deleting files directly


def _get_clutter_categories() -> list[ClutterCategory]:
    """Return platform-specific clutter categories."""
    categories: list[ClutterCategory] = []

    # --- Cross-platform dev caches ---
    # Package manager stores are cleaned through their own CLI so the store
    # index stays consistent — never by deleting files out from under them.
    categories.append(ClutterCategory(
        name="npm Cache",
        description="npm package cache",
        paths=[str(HOME / ".npm/_cacache")],
        cleanup_command="npm cache clean --force",
    ))
    categories.append(ClutterCategory(
        name="pnpm Store",
        description="pnpm content-addressable store",
        paths=[str(HOME / "Library/pnpm/store"), str(HOME / ".pnpm-store")],
        cleanup_command="pnpm store prune",
    ))
    categories.append(ClutterCategory(
        name="Go Module Cache",
        description="Go module download cache",
        paths=[str(HOME / "go/pkg/mod")],
        cleanup_command="go clean -modcache",
    ))
    categories.append(ClutterCategory(
        name="Gradle Cache",
        description="Gradle build cache",
        paths=[str(HOME / ".gradle/caches/*")],
    ))
    categories.append(ClutterCategory(
        name="Old Downloads",
        description="Files in Downloads older than 30 days",
        paths=[str(HOME / "Downloads")],
    ))
    categories.append(ClutterCategory(
        name="node_modules",
        description="Dependency folders of projects untouched for 30+ days (restored by npm install)",
        paths=[str(HOME)],  # searched recursively; handled specially in scanner
    ))

    if IS_MACOS:
        categories.extend(_macos_categories())
    elif IS_WINDOWS:
        categories.extend(_windows_categories())

    return categories


def _macos_categories() -> list[ClutterCategory]:
    """macOS-specific clutter categories."""
    return [
        ClutterCategory(
            name="System Caches",
            description="Application and system caches (rebuilt automatically)",
            paths=[str(HOME / "Library/Caches/*")],
        ),
        ClutterCategory(
            name="System Logs",
            description="Application log files",
            paths=[str(HOME / "Library/Logs/*")],
        ),
        ClutterCategory(
            name="Xcode DerivedData",
            description="Xcode build artifacts (rebuilt on next build)",
            paths=[str(HOME / "Library/Developer/Xcode/DerivedData/*")],
        ),
        ClutterCategory(
            name="Xcode Archives",
            description="Old Xcode build archives",
            paths=[str(HOME / "Library/Developer/Xcode/Archives/*")],
        ),
        ClutterCategory(
            name="Homebrew Cache",
            description="Downloaded Homebrew packages",
            paths=[str(HOME / "Library/Caches/Homebrew/*")],
        ),
        ClutterCategory(
            name="yarn Cache",
            description="Yarn package cache",
            paths=[str(HOME / "Library/Caches/Yarn")],
            cleanup_command="yarn cache clean",
        ),
        ClutterCategory(
            name="pip Cache",
            description="pip download cache",
            paths=[str(HOME / "Library/Caches/pip/*")],
        ),
        ClutterCategory(
            name="CocoaPods Cache",
            description="CocoaPods downloaded pods",
            paths=[str(HOME / "Library/Caches/CocoaPods/*")],
        ),
        ClutterCategory(
            name="Crash Reports",
            description="Application crash reports",
            paths=[str(HOME / "Library/Logs/DiagnosticReports/*")],
        ),
    ]


def _windows_categories() -> list[ClutterCategory]:
    """Windows-specific clutter categories."""
    localappdata = Path(os.environ.get("LOCALAPPDATA", HOME / "AppData/Local"))
    appdata = Path(os.environ.get("APPDATA", HOME / "AppData/Roaming"))
    temp = Path(os.environ.get("TEMP", localappdata / "Temp"))
    windir = Path(os.environ.get("WINDIR", "C:/Windows"))

    return [
        ClutterCategory(
            name="Temp Files",
            description="Windows temporary files",
            paths=[str(temp / "*")],
        ),
        ClutterCategory(
            name="Windows Temp",
            description="System-wide temporary files",
            paths=[str(windir / "Temp/*")],
        ),
        ClutterCategory(
            name="Thumbnail Cache",
            description="Windows Explorer thumbnail cache",
            paths=[str(localappdata / "Microsoft/Windows/Explorer/thumbcache_*")],
        ),
        ClutterCategory(
            name="pip Cache",
            description="pip download cache",
            paths=[str(localappdata / "pip/cache/*")],
        ),
        ClutterCategory(
            name="yarn Cache",
            description="Yarn package cache",
            paths=[str(localappdata / "Yarn/Cache")],
            cleanup_command="yarn cache clean",
        ),
        ClutterCategory(
            name="NuGet Cache",
            description="NuGet package cache",
            paths=[str(HOME / ".nuget/packages/*")],
        ),
        ClutterCategory(
            name="VS Code Cache",
            description="VS Code cached data",
            paths=[str(appdata / "Code/Cache/*"), str(appdata / "Code/CachedData/*")],
        ),
        ClutterCategory(
            name="Crash Dumps",
            description="Windows crash dump files",
            paths=[str(localappdata / "CrashDumps/*")],
        ),
    ]


CLUTTER_CATEGORIES: list[ClutterCategory] = _get_clutter_categories()


# --- Protection tiers ---
#
# Tier 1 — system protected: never suggested, never deleted, no exceptions.
#   OS files, keychains, app bundles, VM runtimes, message/mail/photo databases,
#   iOS device backups, package-manager stores (CLI-managed), kernel extensions.
#
# Tier 2 — user data: never *suggested* automatically, but removable when the
#   user explicitly targets it (e.g. selects the Old Downloads category, or
#   points a command at the directory). Backed up regardless of size when a
#   backup drive is in use.


def _system_protected_prefixes() -> list[Path]:
    prefixes = [
        HOME / ".Trash",
        HOME / ".ssh",
        HOME / ".gnupg",
        # Package manager stores — cleaned via their own CLI only
        HOME / ".npm/_cacache",
        HOME / ".pnpm-store",
        HOME / ".yarn/cache",
        HOME / "go/pkg/mod",
        HOME / "Library/pnpm/store",
        # Container/VM runtimes
        HOME / ".docker",
        HOME / ".lima",
    ]

    if IS_MACOS:
        prefixes += [
            Path("/System"),
            Path("/usr"),
            Path("/bin"),
            Path("/sbin"),
            Path("/private"),
            Path("/etc"),
            Path("/var"),
            Path("/Applications"),
            Path("/Library/Extensions"),
            HOME / "Library/Keychains",
            HOME / "Library/Preferences",
            HOME / "Library/Saved Application State",
            HOME / "Library/Cookies",
            HOME / "Library/Extensions",
            HOME / "Library/Mail",
            HOME / "Library/Messages",
            HOME / "Library/Safari",
            HOME / "Library/Fonts",
            HOME / "Library/Containers",
            HOME / "Library/Group Containers",
            HOME / "Library/CloudStorage",
            HOME / "Library/Application Support/MobileSync/Backup",
            HOME / "Library/Application Support/com.apple.sharedfilelist",
        ]
    elif IS_WINDOWS:
        windir = os.environ.get("WINDIR", "C:\\Windows")
        prefixes += [
            Path(windir),
            Path("C:\\Program Files"),
            Path("C:\\Program Files (x86)"),
            HOME / "AppData/Local/Microsoft",
            HOME / "AppData/Roaming/Microsoft",
        ]

    return prefixes


SYSTEM_PROTECTED_PREFIXES: list[Path] = _system_protected_prefixes()
_PROTECTED_PREFIX_STRS: tuple[str, ...] = tuple(
    str(p) + os.sep for p in SYSTEM_PROTECTED_PREFIXES
)
_PROTECTED_EXACT_STRS: frozenset[str] = frozenset(str(p) for p in SYSTEM_PROTECTED_PREFIXES)

USER_DATA_DIRS: frozenset[Path] = frozenset(
    HOME / d for d in ("Documents", "Desktop", "Pictures", "Music", "Movies", "Downloads")
)
_USER_DATA_PREFIX_STRS: tuple[str, ...] = tuple(str(p) + os.sep for p in USER_DATA_DIRS)
_USER_DATA_EXACT_STRS: frozenset[str] = frozenset(str(p) for p in USER_DATA_DIRS)

PROTECTED_FILENAMES = {".DS_Store"}
PROTECTED_SUFFIXES = (".plist",)

# Paths carrying a personal identifier (e.g. GoogleDrive-user@example.com mounts)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

PROTECTED_BUNDLE_SUFFIXES: tuple[str, ...] = (
    ".app",
    ".photoslibrary",
    ".photolibrary",
    ".imovielibrary",
    ".fcpbundle",
)
_PROTECTED_BUNDLE_MARKERS: tuple[str, ...] = tuple(
    s + os.sep for s in PROTECTED_BUNDLE_SUFFIXES
)
_RUNTIME_MARKERS = tuple(os.sep + d + os.sep for d in RUNTIME_DIR_NAMES)


def is_system_protected(path: Path | str) -> bool:
    """Tier 1: must never be deleted or suggested, no exceptions."""
    s = str(path)
    if s in _PROTECTED_EXACT_STRS or s.startswith(_PROTECTED_PREFIX_STRS):
        return True
    p = Path(s)
    if p.name in PROTECTED_FILENAMES or p.suffix in PROTECTED_SUFFIXES:
        return True
    # Inside an application or photo/media library bundle, or is one
    if any(m in s for m in _PROTECTED_BUNDLE_MARKERS) or any(s.endswith(sfx) for sfx in PROTECTED_BUNDLE_SUFFIXES):
        return True
    # Inside an active VM runtime dir
    if any(m in s for m in _RUNTIME_MARKERS) or p.name in RUNTIME_DIR_NAMES:
        return True
    return False



def is_user_data(path: Path | str) -> bool:
    """Tier 2: personal files — only removable on explicit user action."""
    s = str(path)
    return s in _USER_DATA_EXACT_STRS or s.startswith(_USER_DATA_PREFIX_STRS)


def is_suggestible(path: Path | str) -> bool:
    """May this path appear in automatic cleanup suggestions?"""
    if is_system_protected(path):
        return False
    if _EMAIL_RE.search(str(path)):
        return False
    return True
