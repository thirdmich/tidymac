"""Clutter pattern definitions and protection rules for macOS and Windows cleanup."""

from __future__ import annotations

import os
import re
import shlex
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

# Only commands tidymac itself defines may run. Matched on the exact argv so an
# AI-proposed command can't append flags (e.g. `--prefix /`) to a known one.
_ALLOWED_CLEANUP_ARGVS: frozenset[tuple[str, ...]] = frozenset(
    tuple(shlex.split(c.cleanup_command)) for c in CLUTTER_CATEGORIES if c.cleanup_command
)


def is_allowed_cleanup_command(command: str) -> bool:
    """True only if `command` is exactly one of the built-in package-manager cleanup commands."""
    try:
        return tuple(shlex.split(command)) in _ALLOWED_CLEANUP_ARGVS
    except ValueError:
        return False


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


def fold_path_case(path: Path | str) -> str:
    """Path string normalized for comparison.

    macOS and Windows filesystems ignore case, so `~/.SSH` is the same folder as
    `~/.ssh`; compare case-folded there or a respelled path slips past the rules.
    """
    s = str(path)
    return s.casefold() if IS_MACOS or IS_WINDOWS else s


SYSTEM_PROTECTED_PREFIXES: list[Path] = _system_protected_prefixes()
_PROTECTED_PREFIX_STRS: tuple[str, ...] = tuple(
    fold_path_case(p) + os.sep for p in SYSTEM_PROTECTED_PREFIXES
)
_PROTECTED_EXACT_STRS: frozenset[str] = frozenset(
    fold_path_case(p) for p in SYSTEM_PROTECTED_PREFIXES
)

USER_DATA_DIRS: frozenset[Path] = frozenset(
    HOME / d for d in ("Documents", "Desktop", "Pictures", "Music", "Movies", "Downloads")
)
_USER_DATA_PREFIX_STRS: tuple[str, ...] = tuple(fold_path_case(p) + os.sep for p in USER_DATA_DIRS)
_USER_DATA_EXACT_STRS: frozenset[str] = frozenset(fold_path_case(p) for p in USER_DATA_DIRS)

# Folders macOS requires to exist (it guards them with a deny-delete ACL) but
# whose contents are disposable. Cleanup empties them; the folder itself stays.
CONTENTS_ONLY_DIRS: frozenset[Path] = (
    frozenset({HOME / "Library/Caches", HOME / "Library/Logs"}) if IS_MACOS else frozenset()
)
_CONTENTS_ONLY_STRS: frozenset[str] = frozenset(fold_path_case(p) for p in CONTENTS_ONLY_DIRS)


def is_contents_only_dir(path: Path | str) -> bool:
    """True if `path` is a folder that may be emptied but never removed itself."""
    return fold_path_case(path).rstrip(os.sep) in _CONTENTS_ONLY_STRS


# Removing a folder removes everything in it, so the home folder, user data
# folders, contents-only folders, and every parent of a Tier 1 path are
# guarded as a whole.
_CONTAINER_GUARDED_STRS: tuple[str, ...] = tuple(
    fold_path_case(p)
    for p in (HOME, *USER_DATA_DIRS, *CONTENTS_ONLY_DIRS, *SYSTEM_PROTECTED_PREFIXES)
)

PROTECTED_FILENAMES = {fold_path_case(".DS_Store")}
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
    fold_path_case(s) + os.sep for s in PROTECTED_BUNDLE_SUFFIXES
)
_RUNTIME_MARKERS = tuple(os.sep + fold_path_case(d) + os.sep for d in RUNTIME_DIR_NAMES)


def is_system_protected(path: Path | str) -> bool:
    """Tier 1: must never be deleted or suggested, no exceptions."""
    s = fold_path_case(path)
    if s in _PROTECTED_EXACT_STRS or s.startswith(_PROTECTED_PREFIX_STRS):
        return True
    p = Path(s)
    if p.name in PROTECTED_FILENAMES or p.suffix in PROTECTED_SUFFIXES:
        return True
    # Inside an application or photo/media library bundle, or is one
    if any(m in s for m in _PROTECTED_BUNDLE_MARKERS) or any(s.endswith(fold_path_case(sfx)) for sfx in PROTECTED_BUNDLE_SUFFIXES):
        return True
    # Inside an active VM runtime dir
    if any(m in s for m in _RUNTIME_MARKERS) or p.name in {fold_path_case(d) for d in RUNTIME_DIR_NAMES}:
        return True
    return False


def contains_protected_path(path: Path | str) -> bool:
    """True if `path` is the home folder, a user data folder, or a parent of a Tier 1 path.

    Deleting such a folder would take protected content with it.
    """
    s = fold_path_case(path).rstrip(os.sep)
    if not s or s.endswith(":"):  # filesystem root ("/" or "C:\\")
        return True
    return any(g == s or g.startswith(s + os.sep) for g in _CONTAINER_GUARDED_STRS)



def is_user_data(path: Path | str) -> bool:
    """Tier 2: personal files — only removable on explicit user action."""
    s = fold_path_case(path)
    return s in _USER_DATA_EXACT_STRS or s.startswith(_USER_DATA_PREFIX_STRS)


def is_suggestible(path: Path | str) -> bool:
    """May this path appear in automatic cleanup suggestions?"""
    if is_system_protected(path):
        return False
    if _EMAIL_RE.search(str(path)):
        return False
    return True
