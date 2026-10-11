"""File scanning engine — large files, duplicates, and clutter detection."""

from __future__ import annotations

import glob
import os
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import xxhash
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn

from .patterns import (
    CLUTTER_CATEGORIES,
    DEPENDENCY_DIR_NAMES,
    ClutterCategory,
    is_suggestible,
    is_system_protected,
    is_user_data,
)


@dataclass
class FileInfo:
    path: Path
    size: int
    modified: float

    @property
    def modified_days_ago(self) -> int:
        return int((time.time() - self.modified) / 86400)

    def size_human(self) -> str:
        return format_size(self.size)


@dataclass
class DuplicateGroup:
    file_hash: str
    size: int
    files: list[Path]

    @property
    def wasted_space(self) -> int:
        return self.size * (len(self.files) - 1)

    def size_human(self) -> str:
        return format_size(self.size)

    def wasted_space_human(self) -> str:
        return format_size(self.wasted_space)



@dataclass
class ClutterResult:
    category: ClutterCategory
    total_size: int
    file_count: int
    paths: list[Path]


def format_size(size_bytes: int) -> str:
    """Format bytes into human-readable size."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size_bytes) < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"


def _walk_files(
    root: Path,
    progress: Progress | None = None,
    task_id=None,
    include_user_data: bool = False,
) -> list[FileInfo]:
    """Walk a directory tree and collect file info eligible for suggestions.

    Skips symlinks, system-protected paths, dependency dirs, and app bundles.
    User-data dirs (Documents, Downloads, ...) are skipped unless
    include_user_data is set or the scan root itself points inside one —
    an explicit root is the user flagging that area for cleanup.
    """
    files: list[FileInfo] = []
    root_path = Path(root).expanduser()
    root_is_user_data = is_user_data(root_path)
    try:
        for dirpath, dirnames, filenames in os.walk(root_path, followlinks=False):
            dp = Path(dirpath)
            # Files inside node_modules/.venv/etc. must never be removed
            # individually — they're handled as whole dirs by the clutter scan.
            dirnames[:] = [
                d for d in dirnames
                if d not in DEPENDENCY_DIR_NAMES
                if not d.startswith(".") or d in (".gradle", ".cache")
                if not is_system_protected(os.path.join(dirpath, d))
                if include_user_data
                or root_is_user_data
                or not is_user_data(os.path.join(dirpath, d))
                if not Path(dirpath, d).is_symlink()
            ]

            for fname in filenames:
                fpath = dp / fname
                if not is_suggestible(fpath):
                    continue
                if fpath.is_symlink():
                    continue
                try:
                    stat = fpath.stat()
                    files.append(FileInfo(path=fpath, size=stat.st_size, modified=stat.st_mtime))
                    if progress and task_id is not None:
                        progress.update(task_id, advance=0, description=f"[cyan]Scanning... {len(files)} files found")
                except (PermissionError, OSError):
                    continue
    except (PermissionError, OSError):
        pass
    return files


def scan_large_files(
    root: Path,
    min_size: int = 100 * 1024 * 1024,  # 100 MB
    progress: Progress | None = None,
    include_user_data: bool = False,
) -> list[FileInfo]:
    """Find files larger than min_size under root."""
    task_id = None
    if progress:
        task_id = progress.add_task("[cyan]Scanning for large files...", total=None)

    all_files = _walk_files(root, progress, task_id, include_user_data=include_user_data)
    large = [f for f in all_files if f.size >= min_size]
    large.sort(key=lambda f: f.size, reverse=True)

    if progress and task_id is not None:
        progress.remove_task(task_id)
    return large


def _hash_file(path: Path, partial: bool = False) -> str | None:
    """Hash a file using xxhash. If partial=True, only hash first 4KB."""
    try:
        h = xxhash.xxh128()
        with open(path, "rb") as f:
            if partial:
                chunk = f.read(4096)
                if chunk:
                    h.update(chunk)
            else:
                while True:
                    chunk = f.read(1024 * 1024)  # 1MB chunks
                    if not chunk:
                        break
                    h.update(chunk)
        return h.hexdigest()
    except (PermissionError, OSError):
        return None


def scan_duplicates(
    root: Path,
    min_size: int = 1024,  # Skip files < 1KB
    progress: Progress | None = None,
) -> list[DuplicateGroup]:
    """Find duplicate files using 3-phase approach: size → partial hash → full hash."""
    task_id = None
    if progress:
        task_id = progress.add_task("[cyan]Phase 1: Scanning files...", total=None)

    all_files = _walk_files(root, progress, task_id)

    # Phase 1: Group by size
    if progress and task_id is not None:
        progress.update(task_id, description="[cyan]Phase 1: Grouping by file size...")
    size_groups: dict[int, list[Path]] = defaultdict(list)
    for f in all_files:
        if f.size >= min_size:
            size_groups[f.size].append(f.path)

    candidates = {size: paths for size, paths in size_groups.items() if len(paths) >= 2}

    if progress and task_id is not None:
        progress.update(task_id, description=f"[cyan]Phase 2: Partial hashing {sum(len(p) for p in candidates.values())} files...")

    # Phase 2: Partial hash (first 4KB)
    partial_groups: dict[str, list[Path]] = defaultdict(list)
    for size, paths in candidates.items():
        for p in paths:
            h = _hash_file(p, partial=True)
            if h:
                partial_groups[f"{size}:{h}"].append(p)

    candidates2 = {key: paths for key, paths in partial_groups.items() if len(paths) >= 2}

    if progress and task_id is not None:
        progress.update(task_id, description=f"[cyan]Phase 3: Full hashing {sum(len(p) for p in candidates2.values())} files...")

    # Phase 3: Full hash
    full_groups: dict[str, list[Path]] = defaultdict(list)
    for key, paths in candidates2.items():
        for p in paths:
            h = _hash_file(p, partial=False)
            if h:
                full_groups[h].append(p)

    results = []
    for file_hash, paths in full_groups.items():
        if len(paths) >= 2:
            size = paths[0].stat().st_size
            results.append(DuplicateGroup(file_hash=file_hash, size=size, files=sorted(paths)))

    results.sort(key=lambda g: g.wasted_space, reverse=True)

    if progress and task_id is not None:
        progress.remove_task(task_id)
    return results


STALE_PROJECT_DAYS = 30


def _project_last_modified(project_dir: Path, max_depth: int = 3, max_files: int = 2000) -> float:
    """Most recent mtime among a project's own files, ignoring dependency
    dirs and hidden dirs. Shallow and capped so it stays fast."""
    try:
        latest = project_dir.stat().st_mtime
    except OSError:
        latest = 0.0
    root_depth = len(project_dir.parts)
    checked = 0
    try:
        for dirpath, dirnames, filenames in os.walk(project_dir, followlinks=False):
            depth = len(Path(dirpath).parts) - root_depth
            if depth >= max_depth:
                dirnames[:] = []
            else:
                dirnames[:] = [
                    d for d in dirnames
                    if d not in DEPENDENCY_DIR_NAMES
                    if not d.startswith(".")
                ]
            for fname in filenames:
                try:
                    mt = (Path(dirpath) / fname).stat().st_mtime
                except OSError:
                    continue
                if mt > latest:
                    latest = mt
                checked += 1
                if checked >= max_files:
                    return latest
    except OSError:
        pass
    return latest


def _is_project_stale(dep_dir: Path, stale_days: int = STALE_PROJECT_DAYS) -> bool:
    """True if the project containing dep_dir hasn't been touched recently."""
    cutoff = time.time() - stale_days * 86400
    return _project_last_modified(dep_dir.parent) < cutoff


def _find_dependency_dirs(root: Path, target_name: str, max_depth: int = 6) -> list[Path]:
    """Find directories named target_name under root, without descending into
    them, hidden dirs, or system locations like ~/Library."""
    found: list[Path] = []
    skip_names = {"Library", "Applications", "Pictures", "Music", "Movies"}
    root_depth = len(root.parts)
    try:
        for dirpath, dirnames, _ in os.walk(root, followlinks=False):
            if target_name in dirnames:
                p = Path(dirpath) / target_name
                if not p.is_symlink():
                    found.append(p)
            depth = len(Path(dirpath).parts) - root_depth
            if depth >= max_depth:
                dirnames[:] = []
                continue
            dirnames[:] = [
                d for d in dirnames
                if d != target_name
                if d not in DEPENDENCY_DIR_NAMES
                if d not in skip_names
                if not d.startswith(".")
                if not Path(dirpath, d).is_symlink()
            ]
    except (PermissionError, OSError):
        pass
    return found


def scan_clutter(progress: Progress | None = None) -> list[ClutterResult]:
    """Scan known clutter locations and return categorized results."""
    results: list[ClutterResult] = []
    task_id = None
    if progress:
        task_id = progress.add_task("[cyan]Scanning clutter locations...", total=len(CLUTTER_CATEGORIES))

    for cat in CLUTTER_CATEGORIES:
        total_size = 0
        file_count = 0
        found_paths: list[Path] = []

        for pattern in cat.paths:
            if cat.name == "node_modules":
                # Special handling: whole dependency dirs, found recursively.
                # Only stale projects qualify; sizing is stat-bound, so size
                # dirs in parallel.
                dep_dirs = [
                    d for d in _find_dependency_dirs(Path(pattern), "node_modules")
                    if _is_project_stale(d)
                ]
                with ThreadPoolExecutor(max_workers=8) as executor:
                    sizes = list(executor.map(_dir_size, dep_dirs))
                for dep_dir, ds in zip(dep_dirs, sizes):
                    total_size += ds
                    file_count += 1
                    found_paths.append(dep_dir)
            elif cat.name == "Old Downloads":
                # Special handling: only files older than 30 days
                dl_path = Path(pattern)
                if dl_path.is_dir():
                    cutoff = time.time() - 30 * 86400
                    try:
                        for entry in dl_path.iterdir():
                            if entry.is_symlink() or not is_suggestible(entry):
                                continue
                            try:
                                stat = entry.stat()
                                if stat.st_mtime < cutoff:
                                    if entry.is_file():
                                        total_size += stat.st_size
                                        file_count += 1
                                        found_paths.append(entry)
                                    elif entry.is_dir():
                                        dir_size = _dir_size(entry)
                                        total_size += dir_size
                                        file_count += 1
                                        found_paths.append(entry)
                            except (PermissionError, OSError):
                                continue
                    except (PermissionError, OSError):
                        pass
            else:
                for match in glob.glob(pattern):
                    p = Path(match)
                    if p.is_symlink():
                        continue
                    # CLI-cleaned categories only size their (protected) store;
                    # directly-deleted categories must pass suggestibility.
                    if cat.cleanup_command is None and not is_suggestible(p):
                        continue
                    try:
                        if p.is_file():
                            total_size += p.stat().st_size
                            file_count += 1
                            found_paths.append(p)
                        elif p.is_dir():
                            ds = _dir_size(p)
                            total_size += ds
                            file_count += 1
                            found_paths.append(p)
                    except (PermissionError, OSError):
                        continue

        if total_size > 0:
            results.append(ClutterResult(
                category=cat,
                total_size=total_size,
                file_count=file_count,
                paths=found_paths,
            ))

        if progress and task_id is not None:
            progress.advance(task_id)

    results.sort(key=lambda r: r.total_size, reverse=True)

    if progress and task_id is not None:
        progress.remove_task(task_id)
    return results


def _dir_size(path: Path) -> int:
    """Get total size of a directory."""
    total = 0
    try:
        for dirpath, _, filenames in os.walk(path, followlinks=False):
            for fname in filenames:
                fpath = Path(dirpath) / fname
                if fpath.is_symlink():
                    continue
                try:
                    total += fpath.stat().st_size
                except (PermissionError, OSError):
                    continue
    except (PermissionError, OSError):
        pass
    return total


def get_disk_usage(path: str | None = None) -> tuple[int, int, int]:
    """Return (total, used, free) bytes for the filesystem containing path."""
    import shutil
    import sys
    if path is None:
        path = "C:\\" if sys.platform == "win32" else "/"
    usage = shutil.disk_usage(path)
    return usage.total, usage.used, usage.free


VIDEO_EXTENSIONS: frozenset[str] = frozenset({
    ".mov", ".mp4", ".mkv", ".avi", ".webm", ".m4v", ".wmv", ".flv", ".ts", ".mts"
})
PHOTO_EXTENSIONS: frozenset[str] = frozenset({
    ".heic", ".heif", ".jpg", ".jpeg", ".png", ".raw", ".cr2", ".nef", ".arw",
    ".dng", ".tiff", ".tif", ".gif", ".webp"
})
MEDIA_EXTENSIONS: frozenset[str] = VIDEO_EXTENSIONS | PHOTO_EXTENSIONS


@dataclass
class PhotoLibraryInfo:
    path: Path
    name: str
    size: int
    is_apple_photos: bool

    def size_human(self) -> str:
        return format_size(self.size)


@dataclass
class MediaScanResult:
    large_videos: list[FileInfo]
    screen_recordings: list[FileInfo]
    screenshots: list[FileInfo]
    duplicate_groups: list[DuplicateGroup]
    photo_libraries: list[PhotoLibraryInfo]
    total_video_bytes: int
    total_photo_bytes: int


def scan_media(
    roots: list[Path] | Path | None = None,
    min_video_size_mb: int = 25,
    find_duplicates: bool = True,
    progress: Progress | None = None,
) -> MediaScanResult:
    """Scan for photos, videos, screen recordings, duplicates, and Apple Photos libraries.

    Inspects standalone media across user directories while respecting library protection.
    """
    from .patterns import HOME

    if roots is None:
        user_dirs = [HOME / "Movies", HOME / "Pictures", HOME / "Downloads", HOME / "Desktop"]
        scan_roots = [d for d in user_dirs if d.exists()]
    elif isinstance(roots, (str, Path)):
        scan_roots = [Path(roots).expanduser()]
    else:
        scan_roots = [Path(r).expanduser() for r in roots]

    large_videos: list[FileInfo] = []
    screen_recordings: list[FileInfo] = []
    screenshots: list[FileInfo] = []
    libraries: list[PhotoLibraryInfo] = []
    all_media_files: list[FileInfo] = []
    total_video_bytes = 0
    total_photo_bytes = 0

    video_min_bytes = max(1, min_video_size_mb) * 1024 * 1024

    # 1. Check for Photo / Video library bundles
    checked_libraries: set[Path] = set()
    for root in scan_roots:
        candidates = [root] if root.suffix in (".photoslibrary", ".imovielibrary", ".fcpbundle") else []
        try:
            candidates.extend(root.glob("*.photoslibrary"))
            candidates.extend(root.glob("*.imovielibrary"))
            candidates.extend(root.glob("*.fcpbundle"))
        except (PermissionError, OSError):
            pass

        for lib in candidates:
            if lib in checked_libraries or not lib.exists():
                continue
            checked_libraries.add(lib)
            sz = _dir_size(lib)
            is_apple = lib.suffix == ".photoslibrary"
            libraries.append(
                PhotoLibraryInfo(
                    path=lib,
                    name=lib.name,
                    size=sz,
                    is_apple_photos=is_apple,
                )
            )

    # 2. Walk directories for standalone media files
    for root in scan_roots:
        walked = _walk_files(root, progress=progress, include_user_data=True)
        for f in walked:
            ext = f.path.suffix.lower()
            if ext not in MEDIA_EXTENSIONS:
                continue

            all_media_files.append(f)
            fname_lower = f.path.name.lower()

            if ext in VIDEO_EXTENSIONS:
                total_video_bytes += f.size
                if (
                    "screen recording" in fname_lower
                    or "screenrecording" in fname_lower
                    or "captures" in str(f.path).lower()
                ):
                    screen_recordings.append(f)
                elif f.size >= video_min_bytes:
                    large_videos.append(f)
            elif ext in PHOTO_EXTENSIONS:
                total_photo_bytes += f.size
                if (
                    "screenshot" in fname_lower
                    or "screen shot" in fname_lower
                    or "screenshots" in str(f.path).lower()
                ):
                    screenshots.append(f)

    large_videos.sort(key=lambda x: x.size, reverse=True)
    screen_recordings.sort(key=lambda x: x.size, reverse=True)
    screenshots.sort(key=lambda x: x.size, reverse=True)

    # 3. Duplicate media detection (by size and content hash)
    duplicate_groups: list[DuplicateGroup] = []
    if find_duplicates and all_media_files:
        size_groups: dict[int, list[Path]] = defaultdict(list)
        for f in all_media_files:
            if f.size >= 10240:  # files >= 10KB
                size_groups[f.size].append(f.path)

        for size, paths in size_groups.items():
            if len(paths) < 2:
                continue
            # Hash files
            hash_groups: dict[str, list[Path]] = defaultdict(list)
            for p in paths:
                h = _hash_file(p, partial=True)
                if h:
                    hash_groups[h].append(p)
            for h, hpaths in hash_groups.items():
                if len(hpaths) < 2:
                    continue
                # Full hash
                full_groups: dict[str, list[Path]] = defaultdict(list)
                for p in hpaths:
                    fh = _hash_file(p, partial=False)
                    if fh:
                        full_groups[fh].append(p)
                for fh, fpaths in full_groups.items():
                    if len(fpaths) >= 2:
                        duplicate_groups.append(
                            DuplicateGroup(
                                file_hash=fh,
                                size=size,
                                files=fpaths,
                            )
                        )

    duplicate_groups.sort(key=lambda g: g.wasted_space, reverse=True)

    return MediaScanResult(
        large_videos=large_videos,
        screen_recordings=screen_recordings,
        screenshots=screenshots,
        duplicate_groups=duplicate_groups,
        photo_libraries=libraries,
        total_video_bytes=total_video_bytes,
        total_photo_bytes=total_photo_bytes,
    )

