"""CLI interface for tidymac — rich terminal UI with interactive cleanup."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn
from rich.prompt import Prompt, Confirm
from rich.table import Table

from .scanner import (
    format_size,
    get_disk_usage,
    scan_large_files,
    scan_duplicates,
    scan_clutter,
)
from .cleaner import (
    IS_MACOS,
    eject_drive,
    get_archive_dir,
    get_backup_dir,
    offload_files,
    remove_files,
    run_cleanup_command,
    select_external_drive,
    _volume_root,
)

console = Console()

TRASH_NAME = "Recycle Bin" if sys.platform == "win32" else "Trash"


def _shorten_path(p: Path | str) -> str:
    """Replace home directory with ~ for display."""
    return str(p).replace(str(Path.home()), "~")


def _make_progress() -> Progress:
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=None),
        TimeElapsedColumn(),
        console=console,
    )


def _disk_panel() -> Panel:
    """Create a disk usage overview panel."""
    total, used, free = get_disk_usage()
    pct = used / total * 100

    if pct > 90:
        color = "red"
    elif pct > 75:
        color = "yellow"
    else:
        color = "green"

    bar_width = 40
    filled = int(bar_width * pct / 100)
    bar = f"[{color}]{'█' * filled}[/{color}][dim]{'░' * (bar_width - filled)}[/dim]"

    text = (
        f"  Total:  {format_size(total)}\n"
        f"  Used:   {format_size(used)} ({pct:.1f}%)\n"
        f"  Free:   {format_size(free)}\n\n"
        f"  {bar} {pct:.1f}%"
    )
    return Panel(text, title="[bold]Disk Usage[/bold]", border_style="blue", expand=False)


def _select_items(items: list, label: str = "items") -> list[int]:
    """Let user select items by number. Returns list of 0-based indices."""
    console.print(f"\n[bold]Enter numbers to select {label} (comma-separated, ranges with dash, or 'all'):[/bold]")
    console.print("[dim]Example: 1,3,5-8 or 'all' or 'none' to cancel[/dim]")

    choice = Prompt.ask("Select").strip().lower()
    if choice in ("none", "n", "q", ""):
        return []
    if choice == "all":
        return list(range(len(items)))

    indices: list[int] = []
    for part in choice.split(","):
        part = part.strip()
        if "-" in part:
            try:
                start, end = part.split("-", 1)
                for i in range(int(start) - 1, int(end)):
                    if 0 <= i < len(items):
                        indices.append(i)
            except ValueError:
                continue
        else:
            try:
                idx = int(part) - 1
                if 0 <= idx < len(items):
                    indices.append(idx)
            except ValueError:
                continue
    return sorted(set(indices))


def _setup_backup(backup: bool, needed_bytes: int = 0) -> Path | None:
    """If --backup is set, find an external drive and create a backup dir."""
    if not backup:
        return None
    drive = select_external_drive(console, needed_bytes=needed_bytes)
    if drive is None:
        return None
    backup_dir = get_backup_dir(drive)
    console.print(f"[green]Backup directory:[/green] {backup_dir}\n")
    return backup_dir


def _offer_eject(path: Path | None) -> None:
    """Offer to safely eject the external drive an operation used."""
    if path is None or not IS_MACOS:
        return
    drive = _volume_root(path)
    if not str(drive).startswith("/Volumes/") or not drive.exists():
        return
    if Confirm.ask(f"Eject {drive.name} safely now?", default=True):
        eject_drive(drive, console)


def _clean_category(result, backup_dir: Path | None) -> tuple[int, int]:
    """Clean one clutter category — via its package manager CLI when it has
    one (keeps the store index consistent), otherwise by removing files."""
    cmd = result.category.cleanup_command
    if cmd:
        console.print(f"  [dim]{result.category.name} is cleaned by its package manager, not by deleting files.[/dim]")
        if Confirm.ask(f"  Run [bold]{cmd}[/bold] now?", default=True):
            if run_cleanup_command(cmd, console):
                console.print(f"  [green]Done — reclaims up to {format_size(result.total_size)}[/green]")
                return result.file_count, result.total_size
        else:
            console.print(f"  [dim]Skipped — run `{cmd}` yourself when ready.[/dim]")
        return 0, 0
    return remove_files(result.paths, backup_dir=backup_dir, console=console)


@click.group(epilog="Made by FusionICT — https://fusionict.com")
@click.version_option(package_name="tidymac")
def cli():
    """tidymac — Safe cleanup tool for Mac and Windows.

    Find large files, duplicates, and clutter. All deletions go to
    Trash/Recycle Bin (or to your external drive with --backup).
    """
    pass


@cli.command()
def scan():
    """Show disk usage overview and quick cleanup summary."""
    console.print()
    console.print(_disk_panel())
    console.print()

    with _make_progress() as progress:
        clutter_results = scan_clutter(progress=progress)

    if clutter_results:
        total_clutter = sum(r.total_size for r in clutter_results)
        table = Table(title="Clutter Summary", show_lines=False, expand=False)
        table.add_column("#", style="dim", width=4)
        table.add_column("Category", style="cyan")
        table.add_column("Size", style="bold yellow", justify="right")
        table.add_column("Items", justify="right")

        for i, r in enumerate(clutter_results, 1):
            safe_tag = "" if r.category.safe else " [red](caution)[/red]"
            table.add_row(str(i), f"{r.category.name}{safe_tag}", format_size(r.total_size), str(r.file_count))

        console.print(table)
        console.print(f"\n[bold]Total reclaimable clutter:[/bold] [yellow]{format_size(total_clutter)}[/yellow]")
    else:
        console.print("[green]No significant clutter found![/green]")

    console.print("\n[dim]Run [bold]tidymac large[/bold], [bold]tidymac dupes[/bold], or [bold]tidymac clutter[/bold] for detailed cleanup.[/dim]")
    console.print(f"[dim]Add [bold]--backup[/bold] to any cleanup command to move files to an external drive instead of {TRASH_NAME}.[/dim]")
    console.print("[dim]Made by [link=https://fusionict.com]FusionICT[/link] — fusionict.com[/dim]\n")


@cli.command()
@click.option("--min-size", default="100MB", help="Minimum file size (e.g. 50MB, 1GB)")
@click.option("--path", default=str(Path.home()), help="Directory to scan", type=click.Path(exists=True))
@click.option("--backup", is_flag=True, help="Move files to external drive instead of Trash/Recycle Bin")
def large(min_size: str, path: str, backup: bool):
    """Find large files."""
    size_bytes = _parse_size(min_size)
    scan_path = Path(path)

    console.print(f"\n[bold]Scanning for files larger than {format_size(size_bytes)} in {scan_path}...[/bold]\n")

    with _make_progress() as progress:
        files = scan_large_files(scan_path, min_size=size_bytes, progress=progress)

    if not files:
        console.print("[green]No files found above the size threshold.[/green]\n")
        return

    table = Table(title=f"Large Files ({len(files)} found)", show_lines=False, expand=True)
    table.add_column("#", style="dim", width=4)
    table.add_column("Size", style="bold yellow", justify="right", width=10)
    table.add_column("Modified", style="dim", width=12)
    table.add_column("Path", style="cyan", overflow="fold")

    total_size = 0
    for i, f in enumerate(files, 1):
        mod_date = datetime.fromtimestamp(f.modified).strftime("%Y-%m-%d")
        display_path = _shorten_path(f.path)
        table.add_row(str(i), f.size_human(), mod_date, display_path)
        total_size += f.size

    console.print(table)
    console.print(f"\n[bold]Total:[/bold] [yellow]{format_size(total_size)}[/yellow] across {len(files)} files\n")

    selected = _select_items(files, "files to remove")
    if not selected:
        console.print("[dim]No files selected.[/dim]\n")
        return

    to_remove = [files[i].path for i in selected]
    sel_size = sum(files[i].size for i in selected)

    backup_dir = _setup_backup(backup, needed_bytes=sel_size)
    dest_label = f"external drive ({backup_dir})" if backup_dir else TRASH_NAME

    console.print(f"\n[bold]Moving {len(to_remove)} files ({format_size(sel_size)}) to {dest_label}...[/bold]\n")

    if not Confirm.ask("Confirm?"):
        console.print("[dim]Cancelled.[/dim]\n")
        return

    count, freed = remove_files(to_remove, backup_dir=backup_dir, console=console)
    action = "Backed up" if backup_dir else "Trashed"
    console.print(f"\n[bold green]Done![/bold green] {action} {count} files, freed {format_size(freed)}\n")
    _offer_eject(backup_dir)


@cli.command()
@click.option("--path", default=str(Path.home()), help="Directory to scan", type=click.Path(exists=True))
@click.option("--min-size", default="1KB", help="Minimum file size to consider")
@click.option("--backup", is_flag=True, help="Move files to external drive instead of Trash/Recycle Bin")
def dupes(path: str, min_size: str, backup: bool):
    """Find duplicate files."""
    scan_path = Path(path)
    size_bytes = _parse_size(min_size)

    console.print(f"\n[bold]Scanning for duplicates in {scan_path}...[/bold]\n")

    with _make_progress() as progress:
        groups = scan_duplicates(scan_path, min_size=size_bytes, progress=progress)

    if not groups:
        console.print("[green]No duplicates found![/green]\n")
        return

    total_wasted = sum(g.wasted_space for g in groups)
    console.print(f"[bold]Found {len(groups)} duplicate groups, wasting {format_size(total_wasted)}[/bold]\n")

    # Set up backup once if needed
    backup_dir = _setup_backup(backup) if backup else None

    for gi, group in enumerate(groups):
        table = Table(
            title=f"Group {gi + 1} — {format_size(group.size)} each, {len(group.files)} copies",
            show_lines=False,
            expand=True,
            title_style="bold magenta",
        )
        table.add_column("#", style="dim", width=4)
        table.add_column("Path", style="cyan", overflow="fold")

        for fi, fpath in enumerate(group.files):
            display = _shorten_path(fpath)
            marker = " [green](oldest)[/green]" if fi == 0 else ""
            table.add_row(str(fi + 1), f"{display}{marker}")

        console.print(table)

        console.print(f"  [dim]Wasted space: {format_size(group.wasted_space)}[/dim]")
        console.print(f"  [dim]Enter numbers of copies to REMOVE (keep at least one):[/dim]")

        selected = _select_items(group.files, "copies to remove")
        if not selected:
            console.print("  [dim]Skipped.[/dim]\n")
            continue

        if len(selected) == len(group.files):
            console.print("  [yellow]Can't remove all copies — keeping #1.[/yellow]")
            selected = selected[1:]

        to_remove = [group.files[i] for i in selected]
        count, freed = remove_files(to_remove, backup_dir=backup_dir, console=console)
        action = "Backed up" if backup_dir else "Trashed"
        console.print(f"  [green]{action} {count} copies, freed {format_size(freed)}[/green]\n")

    _offer_eject(backup_dir)


@cli.command()
@click.option("--dry-run", is_flag=True, help="Only show what would be cleaned, don't delete")
@click.option("--backup", is_flag=True, help="Move files to external drive instead of Trash/Recycle Bin")
def clutter(dry_run: bool, backup: bool):
    """Clean up caches, logs, and other clutter."""
    console.print("\n[bold]Scanning for clutter...[/bold]\n")

    with _make_progress() as progress:
        results = scan_clutter(progress=progress)

    if not results:
        console.print("[green]No significant clutter found![/green]\n")
        return

    total_clutter = sum(r.total_size for r in results)

    table = Table(title="Clutter Categories", show_lines=False, expand=False)
    table.add_column("#", style="dim", width=4)
    table.add_column("Category", style="cyan", min_width=20)
    table.add_column("Size", style="bold yellow", justify="right", width=10)
    table.add_column("Items", justify="right", width=6)
    table.add_column("Description", style="dim")
    table.add_column("Safe?", width=6)

    for i, r in enumerate(results, 1):
        safe = "[green]Yes[/green]" if r.category.safe else "[red]No[/red]"
        desc = r.category.description
        if r.category.cleanup_command:
            desc += f" [cyan](via `{r.category.cleanup_command}`)[/cyan]"
        table.add_row(str(i), r.category.name, format_size(r.total_size), str(r.file_count), desc, safe)

    console.print(table)
    console.print(f"\n[bold]Total clutter:[/bold] [yellow]{format_size(total_clutter)}[/yellow]\n")

    if dry_run:
        console.print("[dim]Dry run — no files were removed.[/dim]\n")
        return

    selected = _select_items(results, "categories to clean")
    if not selected:
        console.print("[dim]No categories selected.[/dim]\n")
        return

    # Only directly-deleted categories can go to the backup drive
    deletable_size = sum(
        results[i].total_size for i in selected if not results[i].category.cleanup_command
    )
    backup_dir = _setup_backup(backup, needed_bytes=deletable_size)
    dest_label = f"external drive ({backup_dir})" if backup_dir else TRASH_NAME

    sel_size = sum(results[i].total_size for i in selected)
    sel_names = ", ".join(results[i].category.name for i in selected)
    console.print(f"\n[bold]Will clean: {sel_names}[/bold]")
    console.print(f"[bold]Estimated space freed: {format_size(sel_size)}[/bold]")
    console.print(f"[bold]Destination: {dest_label}[/bold]\n")

    unsafe = [results[i] for i in selected if not results[i].category.safe]
    if unsafe:
        console.print("[yellow]Warning: Some selected categories are marked as NOT safe to remove.[/yellow]")
        console.print("[yellow]Data may not be easily recoverable.[/yellow]\n")

    if not Confirm.ask("Proceed with cleanup?"):
        console.print("[dim]Cancelled.[/dim]\n")
        return

    total_count = 0
    total_freed = 0
    for i in selected:
        r = results[i]
        console.print(f"\n[bold]{r.category.name}:[/bold]")
        count, freed = _clean_category(r, backup_dir)
        total_count += count
        total_freed += freed

    console.print(f"\n[bold green]Done![/bold green] Cleaned {total_count} items, freed {format_size(total_freed)}\n")
    _offer_eject(backup_dir)


@cli.command()
@click.option("--min-size", default="500MB", help="Minimum file size (e.g. 500MB, 2GB)")
@click.option("--path", default=str(Path.home()), help="Directory to scan", type=click.Path(exists=True))
def offload(min_size: str, path: str):
    """Archive large files to an external drive (verified copy, then removed from Mac).

    Unlike --backup cleanup, offloaded files are things worth keeping — old
    projects, media, disk images. Each item is copied to /Archive/{date}/ on
    the drive preserving its folder structure, verified (checksummed if over
    1 GB), then removed from the Mac with a README breadcrumb left behind.
    Files currently in use are skipped.
    """
    size_bytes = _parse_size(min_size)
    scan_path = Path(path)

    console.print(f"\n[bold]Scanning for files larger than {format_size(size_bytes)} to offload...[/bold]\n")

    with _make_progress() as progress:
        files = scan_large_files(
            scan_path, min_size=size_bytes, progress=progress, include_user_data=True
        )

    if not files:
        console.print("[green]No files found above the size threshold.[/green]\n")
        return

    table = Table(title=f"Offload Candidates ({len(files)} found)", show_lines=False, expand=True)
    table.add_column("#", style="dim", width=4)
    table.add_column("Size", style="bold yellow", justify="right", width=10)
    table.add_column("Modified", style="dim", width=12)
    table.add_column("Path", style="cyan", overflow="fold")

    for i, f in enumerate(files, 1):
        mod_date = datetime.fromtimestamp(f.modified).strftime("%Y-%m-%d")
        table.add_row(str(i), f.size_human(), mod_date, _shorten_path(f.path))

    console.print(table)

    selected = _select_items(files, "files to offload")
    if not selected:
        console.print("[dim]No files selected.[/dim]\n")
        return

    to_move = [files[i].path for i in selected]
    sel_size = sum(files[i].size for i in selected)

    drive = select_external_drive(console, needed_bytes=sel_size)
    if drive is None:
        console.print("[dim]Cancelled — offload requires an external drive.[/dim]\n")
        return
    archive_dir = get_archive_dir(drive)

    console.print(f"\n[bold]Archiving {len(to_move)} files ({format_size(sel_size)}) to {archive_dir}...[/bold]")
    console.print("[dim]Each file is copied and verified before removal; a README breadcrumb marks the old location.[/dim]\n")

    if not Confirm.ask("Confirm?"):
        console.print("[dim]Cancelled.[/dim]\n")
        return

    count, moved = offload_files(to_move, archive_dir, console=console)
    console.print(f"\n[bold green]Done![/bold green] Archived {count} files, freed {format_size(moved)}\n")
    _offer_eject(drive)


@cli.command(name="all")
@click.option("--backup", is_flag=True, help="Move files to external drive instead of Trash/Recycle Bin")
def run_all(backup: bool):
    """Full guided cleanup — scan everything and clean interactively."""
    console.print("\n[bold cyan]═══ tidymac — Full System Cleanup ═══[/bold cyan]\n")

    # Disk overview
    console.print(_disk_panel())
    console.print()

    # Set up backup once
    backup_dir = None
    if backup:
        backup_dir = _setup_backup(True)
        if backup_dir is None:
            console.print(f"[yellow]Continuing with {TRASH_NAME} instead.[/yellow]\n")
    dest_label = "external drive" if backup_dir else TRASH_NAME

    # Step 1: Clutter
    console.print("[bold]Step 1: Clutter Cleanup[/bold]\n")
    with _make_progress() as progress:
        clutter_results = scan_clutter(progress=progress)

    if clutter_results:
        total_clutter = sum(r.total_size for r in clutter_results)
        table = Table(show_lines=False, expand=False)
        table.add_column("#", style="dim", width=4)
        table.add_column("Category", style="cyan")
        table.add_column("Size", style="bold yellow", justify="right")
        table.add_column("Safe?", width=6)

        for i, r in enumerate(clutter_results, 1):
            safe = "[green]Yes[/green]" if r.category.safe else "[red]No[/red]"
            table.add_row(str(i), r.category.name, format_size(r.total_size), safe)

        console.print(table)
        console.print(f"[bold]Total clutter: {format_size(total_clutter)}[/bold]\n")

        if Confirm.ask(f"Clean up clutter? (→ {dest_label})"):
            selected = _select_items(clutter_results, "categories to clean")
            if selected:
                for i in selected:
                    r = clutter_results[i]
                    console.print(f"\n[bold]{r.category.name}:[/bold]")
                    _clean_category(r, backup_dir)
    else:
        console.print("[green]No clutter found.[/green]")

    console.print()

    # Step 2: Large files
    console.print("[bold]Step 2: Large Files[/bold]\n")
    if Confirm.ask("Scan for large files (>100MB)?"):
        with _make_progress() as progress:
            large_files = scan_large_files(Path.home(), progress=progress)

        if large_files:
            table = Table(show_lines=False, expand=True)
            table.add_column("#", style="dim", width=4)
            table.add_column("Size", style="bold yellow", justify="right", width=10)
            table.add_column("Path", style="cyan", overflow="fold")

            for i, f in enumerate(large_files[:20], 1):
                display_path = _shorten_path(f.path)
                table.add_row(str(i), f.size_human(), display_path)

            if len(large_files) > 20:
                console.print(f"[dim](showing top 20 of {len(large_files)})[/dim]")
            console.print(table)

            selected = _select_items(large_files[:20], "files to remove")
            if selected:
                to_remove = [large_files[i].path for i in selected]
                if Confirm.ask(f"Move {len(to_remove)} files to {dest_label}?"):
                    remove_files(to_remove, backup_dir=backup_dir, console=console)
        else:
            console.print("[green]No large files found.[/green]")

    console.print()

    # Step 3: Duplicates
    console.print("[bold]Step 3: Duplicates[/bold]\n")
    console.print("[dim]Note: Duplicate scanning of your entire home directory can be slow.[/dim]")
    scan_dir = Prompt.ask("Directory to scan for duplicates", default="~/Downloads")
    scan_path = Path(scan_dir).expanduser()

    if scan_path.is_dir():
        with _make_progress() as progress:
            groups = scan_duplicates(scan_path, progress=progress)

        if groups:
            total_wasted = sum(g.wasted_space for g in groups)
            console.print(f"\n[bold]Found {len(groups)} duplicate groups, wasting {format_size(total_wasted)}[/bold]\n")

            for gi, group in enumerate(groups[:10]):
                console.print(f"[magenta]Group {gi + 1}:[/magenta] {format_size(group.size)} x {len(group.files)} copies")
                for fi, fpath in enumerate(group.files):
                    display = _shorten_path(fpath)
                    console.print(f"  {fi + 1}. {display}")

                selected = _select_items(group.files, "copies to remove")
                if selected:
                    if len(selected) == len(group.files):
                        console.print("  [yellow]Keeping #1 to avoid removing all copies.[/yellow]")
                        selected = selected[1:]
                    to_remove = [group.files[i] for i in selected]
                    remove_files(to_remove, backup_dir=backup_dir, console=console)
                console.print()
        else:
            console.print("[green]No duplicates found.[/green]")

    # Final summary
    console.print("\n[bold cyan]═══ Cleanup Complete ═══[/bold cyan]")
    if backup_dir:
        console.print(f"[dim]Files backed up to: {backup_dir}[/dim]")
        console.print("[dim]You can review and restore files from your external drive.[/dim]")
    console.print(_disk_panel())
    console.print()
    _offer_eject(backup_dir)


@cli.command()
@click.option("--path", "-p", "target_path", type=click.Path(), default=None, help="Root path to search for media files.")
@click.option("--min-size", default="25MB", help="Minimum video size (e.g. 50MB, 1GB).")
@click.option("--photos-only", is_flag=True, help="Scan only photos and screenshots.")
@click.option("--videos-only", is_flag=True, help="Scan only video files.")
@click.option("--no-dupes", is_flag=True, help="Skip checking for duplicate media.")
@click.option("--backup", "backup_mode", is_flag=True, help="Move files to an external drive instead of Trash.")
@click.option("--backup-dir", type=click.Path(), default=None, help="Specific backup directory on external drive.")
def media(target_path, min_size, photos_only, videos_only, no_dupes, backup_mode, backup_dir):
    """Scan and clean up photos, videos, screen recordings, screenshots, and duplicates."""
    from .scanner import scan_media

    console.print()
    console.print(_disk_panel())
    console.print()

    bdir = _setup_backup(backup_mode) if backup_mode else None
    min_bytes = _parse_size(min_size)
    min_mb = max(1, min_bytes // (1024 * 1024))

    root = Path(target_path).expanduser() if target_path else None
    display_root = str(root) if root else "Standard media folders (~/Movies, ~/Pictures, ~/Downloads, ~/Desktop)"
    console.print(f"[bold]Scanning for media files in:[/bold] {display_root}\n")

    with _make_progress() as progress:
        res = scan_media(
            roots=root,
            min_video_size_mb=min_mb,
            find_duplicates=not no_dupes,
            progress=progress,
        )


    # 1. Report photo / video libraries
    if res.photo_libraries and not videos_only:
        table = Table(title="Detected Photo & Video Libraries", show_lines=False, expand=True)
        table.add_column("Library", style="bold")
        table.add_column("Path", style="dim")
        table.add_column("Size", justify="right")
        table.add_column("Status / Policy", style="cyan")

        for lib in res.photo_libraries:
            status = "[yellow]Protected (Manage in Apple Photos)[/yellow]" if lib.is_apple_photos else "[blue]Project Bundle[/blue]"
            table.add_row(lib.name, _shorten_path(lib.path), lib.size_human(), status)
        console.print(table)
        console.print("[dim]  ℹ️ Photo libraries are protected from direct file deletion to prevent database corruption.[/dim]\n")

    # 2. Large standalone videos & Screen recordings
    if not photos_only:
        all_videos = res.screen_recordings + [v for v in res.large_videos if v not in res.screen_recordings]
        if all_videos:
            table = Table(title=f"Large Videos & Screen Recordings ({len(all_videos)} files)", show_lines=False, expand=True)
            table.add_column("#", justify="right", style="dim", width=4)
            table.add_column("Type", style="cyan", width=18)
            table.add_column("Filename", style="bold")
            table.add_column("Path", style="dim")
            table.add_column("Size", justify="right")

            for idx, v in enumerate(all_videos[:25], 1):
                v_type = "Screen Recording" if v in res.screen_recordings else "Video File"
                table.add_row(str(idx), v_type, v.path.name, _shorten_path(v.path.parent), v.size_human())
            console.print(table)

            selected = _select_items([v.path for v in all_videos[:25]], "videos to remove")
            if selected:
                to_remove = [all_videos[i].path for i in selected]
                remove_files(to_remove, backup_dir=bdir, console=console)
            console.print()

    # 3. Screenshots
    if not videos_only and res.screenshots:
        table = Table(title=f"Screenshots ({len(res.screenshots)} files)", show_lines=False, expand=True)

        table.add_column("#", justify="right", style="dim", width=4)
        table.add_column("Filename", style="bold")
        table.add_column("Path", style="dim")
        table.add_column("Size", justify="right")

        for idx, s in enumerate(res.screenshots[:20], 1):
            table.add_row(str(idx), s.path.name, _shorten_path(s.path.parent), s.size_human())
        console.print(table)

        selected = _select_items([s.path for s in res.screenshots[:20]], "screenshots to remove")
        if selected:
            to_remove = [res.screenshots[i].path for i in selected]
            remove_files(to_remove, backup_dir=bdir, console=console)
        console.print()

    # 4. Duplicate Media
    if not no_dupes and res.duplicate_groups:
        console.print(f"[bold]Duplicate Media Groups ({len(res.duplicate_groups)} groups):[/bold]")
        for gi, group in enumerate(res.duplicate_groups[:10]):
            console.print(
                f"\n[bold cyan]Group {gi + 1}:[/bold cyan] {len(group.files)} copies of "
                f"[bold]{group.files[0].name}[/bold] ({group.size_human()} each, "
                f"[red]{group.wasted_space_human()} wasted[/red])"
            )
            for fi, fpath in enumerate(group.files):
                console.print(f"  {fi + 1}. {_shorten_path(fpath)}")

            selected = _select_items(group.files, "duplicate copies to remove")
            if selected:
                if len(selected) == len(group.files):
                    console.print("  [yellow]Keeping #1 to avoid removing all copies.[/yellow]")
                    selected = selected[1:]
                to_remove = [group.files[i] for i in selected]
                remove_files(to_remove, backup_dir=bdir, console=console)
            console.print()

    console.print(_disk_panel())
    _offer_eject(bdir)


class CopilotGroup(click.Group):

    """Click group that delegates unrecognized positional args to a default command."""

    def __init__(self, *args, **kwargs):
        self.default_cmd_name = kwargs.pop("default_cmd_name", "query")
        super().__init__(*args, **kwargs)

    def resolve_command(self, ctx, args):
        cmd_name = args[0] if args else None
        if cmd_name in self.commands:
            return super().resolve_command(ctx, args)
        if self.default_cmd_name and self.default_cmd_name in self.commands:
            return self.default_cmd_name, self.commands[self.default_cmd_name], args
        return super().resolve_command(ctx, args)


@cli.group(
    cls=CopilotGroup,
    name="ai",
    default_cmd_name="query",
    invoke_without_command=True,
)
@click.pass_context
def ai(ctx):
    """TidyCopilot — AI-powered autonomous storage optimization.

    Run with a prompt (e.g. `tidymac ai "free up 10GB"`), start an interactive
    chat session (`tidymac ai --chat`), or manage AI settings (`tidymac ai config`).
    """
    if ctx.invoked_subcommand is None:
        from .ai import TidyCopilot

        copilot = TidyCopilot()
        copilot.interactive_chat()


@ai.command(name="query")
@click.argument("prompt", nargs=-1, required=False)
@click.option("--chat", "-c", is_flag=True, help="Launch interactive conversational chat with TidyCopilot")
@click.option("--provider", "-p", help="LLM provider: ollama, gemini, openai, anthropic, or heuristic")
@click.option("--model", "-m", help="Model name (e.g. llama3.2, gemini-3.8-flash, gpt-4o-mini)")
@click.option("--backup", is_flag=True, help="Move files to external drive instead of Trash/Recycle Bin")
def ai_query(prompt, chat, provider, model, backup):
    """Run an AI query or start interactive chat."""
    from .ai import TidyCopilot

    copilot = TidyCopilot(
        provider_name=provider,
        model_name=model,
        use_backup=backup,
    )

    full_prompt = " ".join(prompt).strip() if prompt else ""
    if chat or not full_prompt:
        copilot.interactive_chat()
    else:
        copilot.run_turn(full_prompt)



@ai.command(name="config")
@click.option("--provider", "-p", help="Set default provider (auto, ollama, gemini, openai, anthropic, heuristic)")
@click.option("--gemini-key", help="Set Google Gemini API key")
@click.option("--openai-key", help="Set OpenAI API key")
@click.option("--anthropic-key", help="Set Anthropic API key")
@click.option("--ollama-host", help="Set Ollama host (default: http://localhost:11434)")
@click.option("--ollama-model", help="Set Ollama default model (default: llama3.2)")
def ai_config(provider, gemini_key, openai_key, anthropic_key, ollama_host, ollama_model):
    """View or update AI Copilot settings."""
    from .ai.config import load_ai_config, save_ai_config, get_active_provider, is_ollama_online

    cfg = load_ai_config()
    modified = False

    if provider:
        cfg.provider = provider
        modified = True
    if gemini_key:
        cfg.gemini_api_key = gemini_key
        modified = True
    if openai_key:
        cfg.openai_api_key = openai_key
        modified = True
    if anthropic_key:
        cfg.anthropic_api_key = anthropic_key
        modified = True
    if ollama_host:
        cfg.ollama_host = ollama_host
        modified = True
    if ollama_model:
        cfg.ollama_model = ollama_model
        modified = True

    if modified:
        path = save_ai_config(cfg)
        console.print(f"[green]AI configuration saved to:[/green] {path}\n")

    active_name, reason = get_active_provider(cfg)
    ollama_status = "[green]Online[/green]" if is_ollama_online(cfg.ollama_host) else "[dim]Not reachable[/dim]"

    table = Table(title="TidyCopilot Configuration", show_lines=False)
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="bold")

    table.add_row("Configured Provider", cfg.provider)
    table.add_row("Active Provider", f"{active_name} ({reason})")
    table.add_row("Ollama Host", f"{cfg.ollama_host} ({ollama_status})")
    table.add_row("Ollama Model", cfg.ollama_model)
    table.add_row("Gemini API Key", "***configured***" if cfg.gemini_api_key else "[dim]not set[/dim]")
    table.add_row("OpenAI API Key", "***configured***" if cfg.openai_api_key else "[dim]not set[/dim]")
    table.add_row("Anthropic API Key", "***configured***" if cfg.anthropic_api_key else "[dim]not set[/dim]")

    console.print(table)


@ai.command(name="status")
def ai_status():
    """Display active AI provider and connection status."""
    from .ai.config import load_ai_config, get_active_provider, is_ollama_online

    cfg = load_ai_config()
    active_name, reason = get_active_provider(cfg)
    ollama_ok = is_ollama_online(cfg.ollama_host)

    console.print(f"\n[bold cyan]TidyCopilot Status[/bold cyan]")
    console.print(f"  • [bold]Active Provider:[/bold] [green]{active_name}[/green] ({reason})")
    console.print(f"  • [bold]Local Ollama Server:[/bold] {'[green]Connected[/green]' if ollama_ok else '[dim]Not running[/dim]'}")
    console.print(f"  • [bold]Privacy Guarantee:[/bold] {'100% on-device (Zero data leaves machine)' if active_name in ('ollama', 'heuristic') else 'Cloud API (HTTPS)'}\n")



def _parse_size(size_str: str) -> int:
    """Parse human-readable size string to bytes."""
    size_str = size_str.strip().upper()
    multipliers = {
        "B": 1,
        "KB": 1024,
        "MB": 1024 ** 2,
        "GB": 1024 ** 3,
        "TB": 1024 ** 4,
    }
    for suffix, mult in sorted(multipliers.items(), key=lambda x: -len(x[0])):
        if size_str.endswith(suffix):
            num = size_str[: -len(suffix)].strip()
            return int(float(num) * mult)
    return int(float(size_str))


if __name__ == "__main__":
    cli()
