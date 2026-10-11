"""Safe file removal — external archive/backup with verification, or system Trash.

External drives are write-only targets: tidymac copies to them and deletes
from the Mac, but never deletes, formats, or reorganizes anything already on
the drive (partial copies from a failed verify are the one exception).
"""

from __future__ import annotations

import os
import re
import shutil
import string
import subprocess
import sys
import time
from pathlib import Path

import xxhash
from rich.console import Console
from rich.prompt import Prompt

from send2trash import send2trash

from .patterns import (
    IS_MACOS,
    IS_WINDOWS,
    contains_protected_path,
    is_allowed_cleanup_command,
    is_system_protected,
    is_user_data,
)
from .scanner import format_size

console = Console()

BACKUP_ROOT_NAME = "Backup"    # Mode 2: backup-before-delete
ARCHIVE_ROOT_NAME = "Archive"  # Mode 1: archive offload

# Mode 2: files at or above this size are backed up before deletion;
# smaller files are removed directly. User data is always backed up.
BACKUP_SIZE_THRESHOLD = 100 * 1024 * 1024
# Verify copies of files at or above this size with a full checksum.
CHECKSUM_MIN_SIZE = 1024 ** 3

# Minimum headroom on the target drive relative to the bytes being moved.
DRIVE_SPACE_MARGIN = 1.1


def find_external_drives() -> list[Path]:
    """Find mounted external/removable drives (cross-platform).

    Excludes Time Machine volumes and read-only volumes.
    """
    if IS_MACOS:
        return _find_macos_drives()
    elif IS_WINDOWS:
        return _find_windows_drives()
    # Linux fallback: check /media and /mnt
    drives = []
    for mount_root in ("/media", "/mnt"):
        root = Path(mount_root)
        if root.is_dir():
            for entry in root.iterdir():
                if entry.is_dir() and not entry.is_symlink():
                    if _is_writable(entry):
                        drives.append(entry)
    return drives


def _is_time_machine_volume(volume: Path) -> bool:
    return (
        (volume / ".com.apple.TimeMachine.supported").exists()
        or (volume / "Backups.backupdb").exists()
    )


def _find_macos_drives() -> list[Path]:
    """Find mounted external volumes on macOS."""
    volumes = Path("/Volumes")
    drives = []
    if volumes.is_dir():
        for entry in volumes.iterdir():
            if entry.name == "Macintosh HD" or entry.is_symlink():
                continue
            if not entry.is_dir() or _is_time_machine_volume(entry):
                continue
            if _is_writable(entry):  # also excludes read-only volumes
                drives.append(entry)
    return drives


def _find_windows_drives() -> list[Path]:
    """Find removable/external drives on Windows."""
    import ctypes
    drives = []
    bitmask = ctypes.windll.kernel32.GetLogicalDrives()  # type: ignore[attr-defined]
    for i, letter in enumerate(string.ascii_uppercase):
        if bitmask & (1 << i):
            drive_path = Path(f"{letter}:\\")
            # GetDriveTypeW: 2=removable, 3=fixed, 4=network, 5=cdrom, 6=ramdisk
            drive_type = ctypes.windll.kernel32.GetDriveTypeW(f"{letter}:\\")  # type: ignore[attr-defined]
            if drive_type in (2, 3) and letter not in ("C",):
                if _is_writable(drive_path):
                    drives.append(drive_path)
    return drives


def _is_writable(path: Path) -> bool:
    """Check if a path is writable by creating a test file."""
    try:
        test_file = path / ".tidymac_test"
        test_file.touch()
        test_file.unlink()
        return True
    except (PermissionError, OSError):
        return False


def has_dirty_unmount_indicators(drive: Path) -> bool:
    """Heuristic check for a previous unsafe eject: fsck recovery folders
    that macOS creates when repairing a FAT/ExFAT volume mounted dirty."""
    try:
        return any(
            (drive / name).exists()
            for name in ("FOUND.000", "FOUND.001", "FOUND.002", "fsck")
        )
    except OSError:
        return False


def drive_free_bytes(drive: Path) -> int:
    try:
        return shutil.disk_usage(str(drive)).free
    except OSError:
        return 0


def has_space_for(drive: Path, needed_bytes: int) -> bool:
    return drive_free_bytes(drive) >= int(needed_bytes * DRIVE_SPACE_MARGIN)


def select_external_drive(console: Console, needed_bytes: int = 0) -> Path | None:
    """Find or prompt user to connect an external drive. Returns the drive root."""
    drives = find_external_drives()

    if not drives:
        console.print("\n[yellow]No external drive detected.[/yellow]")
        console.print("[bold]Please plug in your external storage device.[/bold]")
        console.print("[dim]Waiting... (press Ctrl+C to cancel)[/dim]\n")

        for _ in range(60):
            time.sleep(1)
            drives = find_external_drives()
            if drives:
                break

    if not drives:
        console.print("[red]No external drive found after waiting.[/red]")
        return None

    if needed_bytes:
        eligible = [d for d in drives if has_space_for(d, needed_bytes)]
        for d in drives:
            if d not in eligible:
                console.print(
                    f"[yellow]Skipping {d.name}: not enough free space "
                    f"({format_size(drive_free_bytes(d))} free, "
                    f"{format_size(int(needed_bytes * DRIVE_SPACE_MARGIN))} needed).[/yellow]"
                )
        drives = eligible
        if not drives:
            console.print("[red]No connected drive has enough free space.[/red]")
            return None

    if len(drives) == 1:
        drive = drives[0]
    else:
        console.print("\n[bold]Multiple external drives found:[/bold]")
        for i, d in enumerate(drives, 1):
            console.print(f"  {i}. {d.name} ({format_size(drive_free_bytes(d))} free)")
        choice = Prompt.ask("Select drive", choices=[str(i) for i in range(1, len(drives) + 1)])
        drive = drives[int(choice) - 1]

    free = drive_free_bytes(drive)
    console.print(f"[green]External drive:[/green] [bold]{drive.name}[/bold] — {format_size(free)} free")
    if needed_bytes:
        console.print(f"[dim]Estimated free after operation: {format_size(free - needed_bytes)}[/dim]")
    if has_dirty_unmount_indicators(drive):
        console.print(
            "[yellow]Warning: this drive shows signs of a previous unsafe eject "
            "(fsck recovery folders found). Consider running First Aid on it.[/yellow]"
        )
    return drive


def get_backup_dir(drive: Path) -> Path:
    """Create and return the dated backup-before-delete directory on the drive."""
    backup_dir = drive / BACKUP_ROOT_NAME / time.strftime("%Y-%m-%d")
    backup_dir.mkdir(parents=True, exist_ok=True)
    return backup_dir


def get_archive_dir(drive: Path) -> Path:
    """Create and return the dated archive-offload directory on the drive."""
    archive_dir = drive / ARCHIVE_ROOT_NAME / time.strftime("%Y-%m-%d")
    archive_dir.mkdir(parents=True, exist_ok=True)
    return archive_dir


def is_safe_to_remove(path: Path) -> bool:
    """Verify a path may be removed from the Mac."""
    resolved = path.resolve()
    if is_system_protected(resolved) or contains_protected_path(resolved):
        return False
    if IS_MACOS:
        # External drives are write-only targets — never delete from them.
        if str(resolved).startswith("/Volumes/"):
            return False
        if resolved.parts and len(resolved.parts) <= 2 and resolved.parts[0] == "/":
            return False
    if IS_WINDOWS:
        resolved_lower = str(resolved).lower()
        if resolved_lower.startswith("c:\\windows") or resolved_lower.startswith("c:\\program files"):
            return False
    return True


def _volume_root(path: Path) -> Path:
    """The mount root that must stay present for an operation on path."""
    parts = path.parts
    if IS_MACOS and len(parts) >= 3 and parts[1] == "Volumes":
        return Path(parts[0], parts[1], parts[2])
    if IS_WINDOWS:
        return Path(path.anchor)
    return Path(parts[0]) if parts else path


def _wait_for_drive(drive_root: Path, console: Console | None) -> None:
    """If the target drive disappeared mid-operation, pause until it returns."""
    if drive_root.exists():
        return
    if console:
        console.print(
            f"\n[red bold]Drive {drive_root.name} disconnected mid-operation![/red bold]"
        )
        console.print("[yellow]Paused. Reconnect the drive to resume (Ctrl+C to abort).[/yellow]")
    while not drive_root.exists():
        time.sleep(2)
    time.sleep(2)  # let the volume settle after remount
    if console:
        console.print(f"[green]Drive {drive_root.name} reconnected — resuming.[/green]")


def _is_in_use(path: Path) -> bool:
    """Check whether a file/dir is currently open via lsof. Errs toward 'in use'."""
    if IS_WINDOWS:
        return False
    cmd = ["lsof", "-t"]
    cmd += ["+D", str(path)] if path.is_dir() else ["--", str(path)]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=20)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return True  # can't tell — treat as in use, prefer the false negative
    return bool(result.stdout.strip())


def _checksum(path: Path) -> str | None:
    try:
        h = xxhash.xxh128()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(4 * 1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except (PermissionError, OSError):
        return None


def _verify_copy(src: Path, dest: Path, size: int) -> bool:
    """Verify a copy landed intact before the original may be removed."""
    if not dest.exists():
        return False
    if src.is_dir():
        return _dir_size_quick(dest) >= _dir_size_quick(src)
    try:
        if dest.stat().st_size != src.stat().st_size:
            return False
    except OSError:
        return False
    if size >= CHECKSUM_MIN_SIZE:
        src_sum = _checksum(src)
        return src_sum is not None and src_sum == _checksum(dest)
    return True


def _copy_to_drive(src: Path, dest: Path) -> None:
    """Copy src to dest, tolerating metadata failures on ExFAT/FAT targets."""
    if src.is_dir():
        try:
            shutil.copytree(str(src), str(dest))
        except OSError:
            if dest.exists():
                shutil.rmtree(str(dest), ignore_errors=True)
            shutil.copytree(str(src), str(dest), copy_function=shutil.copy)
    else:
        try:
            shutil.copy2(str(src), str(dest))
        except OSError:
            shutil.copy(str(src), str(dest))


def _delete_local(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(str(path))
    else:
        path.unlink()


def _sanitize_for_exfat(name: str) -> str:
    """Sanitize a single path component for ExFAT compatibility."""
    # ExFAT forbids: / \ : * ? " < > | and control chars 0x00-0x1F
    sanitized = re.sub(r'[\\:*?"<>|\x00-\x1f]', '_', name)
    # Truncate to 255 chars (ExFAT max component length)
    if len(sanitized.encode('utf-8')) > 255:
        stem, _, ext = sanitized.rpartition('.')
        if ext and len(ext) < 20:
            max_stem = 255 - len(ext.encode('utf-8')) - 1
            while len(stem.encode('utf-8')) > max_stem:
                stem = stem[:-1]
            sanitized = f"{stem}.{ext}"
        else:
            while len(sanitized.encode('utf-8')) > 255:
                sanitized = sanitized[:-1]
    return sanitized


def _relative_dest_path(original: Path, dest_root: Path) -> Path:
    """Destination under dest_root that preserves the original structure."""
    try:
        if IS_WINDOWS:
            resolved = original.resolve()
            drive = resolved.drive.rstrip(":")  # "C"
            rel = Path(drive) / resolved.relative_to(resolved.anchor)
        else:
            rel = original.resolve().relative_to("/")
    except ValueError:
        rel = Path(original.name)
    rel = Path(*[_sanitize_for_exfat(part) for part in rel.parts])
    dest = dest_root / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        base, suffix = dest.stem, dest.suffix
        counter = 1
        while dest.exists():
            dest = dest.parent / f"{base}_{counter}{suffix}"
            counter += 1
    return dest


def _transfer_to_drive(
    p: Path, dest_root: Path, console: Console | None, label: str
) -> tuple[int, Path]:
    """Copy p to the drive, verify, then delete the original.

    Returns (bytes moved, destination), or raises RuntimeError on
    verification failure.
    """
    size = p.stat().st_size if p.is_file() else _dir_size_quick(p)
    dest = _relative_dest_path(p, dest_root)
    _copy_to_drive(p, dest)
    if not _verify_copy(p, dest, size):
        # Remove only our own failed partial copy — never user files on the drive
        if dest.is_dir():
            shutil.rmtree(str(dest), ignore_errors=True)
        elif dest.exists():
            dest.unlink(missing_ok=True)
        raise RuntimeError("copy verification failed — original kept on Mac")
    _delete_local(p)
    if console:
        console.print(f"  [green]{label}:[/green] {p} ({format_size(size)})")
    return size, dest


def backup_files(
    paths: list[Path],
    backup_dir: Path,
    console: Console | None = None,
    threshold: int = BACKUP_SIZE_THRESHOLD,
) -> tuple[int, int]:
    """Mode 2 — backup before delete. Returns (success_count, total_bytes_freed).

    Files at or above the threshold (and all user data, regardless of size)
    are copied to the drive and verified before the original is deleted.
    Smaller files are deleted directly.
    """
    drive_root = _volume_root(backup_dir)
    success_count = 0
    total_freed = 0

    for p in paths:
        if not p.exists():
            continue
        if not is_safe_to_remove(p):
            if console:
                console.print(f"  [red]Skipped (protected):[/red] {p}")
            continue
        try:
            size = p.stat().st_size if p.is_file() else _dir_size_quick(p)
            if size < threshold and not is_user_data(p):
                _delete_local(p)
                if console:
                    console.print(f"  [green]Deleted:[/green] {p} ({format_size(size)}) [dim](under backup threshold)[/dim]")
            else:
                _wait_for_drive(drive_root, console)
                _transfer_to_drive(p, backup_dir, console, "Backed up")
            success_count += 1
            total_freed += size
        except Exception as e:
            if console:
                console.print(f"  [red]Failed:[/red] {p} — {e}")

    return success_count, total_freed


def offload_files(
    paths: list[Path],
    archive_dir: Path,
    console: Console | None = None,
) -> tuple[int, int]:
    """Mode 1 — archive offload. Returns (success_count, total_bytes_moved).

    Every item is copied to the drive and verified before the original is
    removed; a README breadcrumb is left at the original location. Items
    currently open (per lsof) are skipped.
    """
    drive_root = _volume_root(archive_dir)
    success_count = 0
    total_moved = 0

    for p in paths:
        if not p.exists():
            continue
        if not is_safe_to_remove(p):
            if console:
                console.print(f"  [red]Skipped (protected):[/red] {p}")
            continue
        if _is_in_use(p):
            if console:
                console.print(f"  [yellow]Skipped (currently in use):[/yellow] {p}")
            continue
        try:
            _wait_for_drive(drive_root, console)
            size, dest = _transfer_to_drive(p, archive_dir, console, "Archived")
            _write_breadcrumb(p, dest)
            success_count += 1
            total_moved += size
        except Exception as e:
            if console:
                console.print(f"  [red]Failed:[/red] {p} — {e}")

    return success_count, total_moved


def _write_breadcrumb(original: Path, dest: Path) -> None:
    """Leave a note at the original location saying where the item went."""
    note = original.parent / f"{original.name}.README.txt"
    try:
        note.write_text(
            f"'{original.name}' was moved by tidymac on "
            f"{time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"New location: {dest}\n"
        )
    except OSError:
        pass  # breadcrumb is best-effort


def trash_files(paths: list[Path], console: Console | None = None) -> tuple[int, int]:
    """Move files to system Trash/Recycle Bin. Returns (success_count, total_bytes_freed)."""
    success_count = 0
    total_freed = 0

    for p in paths:
        if not p.exists():
            continue
        if not is_safe_to_remove(p):
            if console:
                console.print(f"  [red]Skipped (protected):[/red] {p}")
            continue
        try:
            size = p.stat().st_size if p.is_file() else _dir_size_quick(p)
            send2trash(str(p))
            success_count += 1
            total_freed += size
            if console:
                label = "Recycled" if IS_WINDOWS else "Trashed"
                console.print(f"  [green]{label}:[/green] {p} ({format_size(size)})")
        except Exception as e:
            if console:
                console.print(f"  [red]Failed:[/red] {p} — {e}")

    return success_count, total_freed


def remove_files(
    paths: list[Path],
    backup_dir: Path | None = None,
    console: Console | None = None,
) -> tuple[int, int]:
    """Remove files — backup-before-delete if a drive is set, otherwise Trash."""
    if backup_dir:
        return backup_files(paths, backup_dir, console)
    return trash_files(paths, console)


def run_cleanup_command(command: str, console: Console | None = None) -> bool:
    """Run a package manager's own cleanup command (e.g. `npm cache clean`)."""
    import shlex
    if not is_allowed_cleanup_command(command):
        if console:
            console.print(f"  [red]Refused to run `{command}`: not a known cleanup command[/red]")
        return False
    try:
        result = subprocess.run(shlex.split(command), capture_output=True, text=True, timeout=600)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        if console:
            console.print(f"  [red]Failed to run `{command}`: {e}[/red]")
        return False
    if result.returncode != 0:
        if console:
            detail = (result.stderr or result.stdout or "").strip().splitlines()
            console.print(f"  [red]`{command}` exited with {result.returncode}[/red]")
            if detail:
                console.print(f"  [dim]{detail[-1]}[/dim]")
        return False
    return True


def eject_drive(drive: Path, console: Console | None = None) -> bool:
    """Safely eject an external drive after operations complete (macOS)."""
    if not IS_MACOS:
        return False
    try:
        result = subprocess.run(
            ["diskutil", "eject", str(drive)], capture_output=True, text=True, timeout=60
        )
    except (subprocess.TimeoutExpired, OSError):
        result = None
    ok = result is not None and result.returncode == 0
    if console:
        if ok:
            console.print(f"[green]Ejected {drive.name} safely.[/green]")
        else:
            console.print(
                f"[yellow]Could not eject {drive.name} automatically — "
                "eject it from Finder before unplugging.[/yellow]"
            )
    return ok


def _dir_size_quick(path: Path) -> int:
    """Quick directory size calculation."""
    total = 0
    try:
        for dirpath, _, filenames in os.walk(path, followlinks=False):
            for fname in filenames:
                try:
                    total += (Path(dirpath) / fname).stat().st_size
                except (PermissionError, OSError):
                    continue
    except (PermissionError, OSError):
        pass
    return total
