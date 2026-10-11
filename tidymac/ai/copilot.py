"""TidyCopilot agent — multi-turn ReAct orchestration with Rich terminal UI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from .config import AIConfig, get_active_provider, load_ai_config
from .prompts import SYSTEM_PROMPT
from .providers import BaseLLMProvider, ChatMessage, ToolCall, create_provider
from .tools import TOOLS_SCHEMA, execute_tool
from ..cleaner import (
    IS_MACOS,
    _volume_root,
    eject_drive,
    get_backup_dir,
    remove_files,
    run_cleanup_command,
    select_external_drive,
)
from ..scanner import format_size


def _offer_drive_eject(backup_dir: Path | None, console: Console) -> None:
    if backup_dir is None or not IS_MACOS:
        return
    drive = _volume_root(backup_dir)
    if not str(drive).startswith("/Volumes/") or not drive.exists():
        return
    if Confirm.ask(f"Eject {drive.name} safely now?", default=True):
        eject_drive(drive, console)



class TidyCopilot:
    """Intelligent autonomous disk optimization agent."""

    def __init__(
        self,
        provider: BaseLLMProvider | None = None,
        provider_name: str | None = None,
        model_name: str | None = None,
        console: Console | None = None,
        use_backup: bool = False,
        max_turns: int = 10,
    ) -> None:
        self.console = console or Console()
        self.cfg = load_ai_config()
        self.provider = provider or create_provider(
            name=provider_name,
            cfg=self.cfg,
            model_override=model_name,
        )
        self.use_backup = use_backup
        self.max_turns = max_turns
        self.conversation_history: list[ChatMessage] = [
            ChatMessage(role="system", content=SYSTEM_PROMPT)
        ]

    def _render_plan(self, plan_data: dict[str, Any]) -> bool:
        """Render a proposed cleanup plan in a Rich table and prompt user for confirmation."""
        title = escape(str(plan_data.get("title", "Proposed Cleanup Plan")))
        items = plan_data.get("items", [])
        total_human = plan_data.get("total_human", "0 B")
        blocked = plan_data.get("blocked_count", 0)

        table = Table(
            title=f"📋 {title} — Reclaimable: [bold yellow]{total_human}[/bold yellow]",
            show_lines=False,
            expand=True,
        )
        table.add_column("#", style="dim", width=4)
        table.add_column("Category", style="cyan", width=20)
        table.add_column("Target / Command", style="white", overflow="fold")
        table.add_column("Size", style="bold yellow", justify="right", width=12)
        table.add_column("Status", width=10)

        deletable_paths: list[Path] = []
        cleanup_cmds: list[str] = []
        total_executable_bytes = 0

        for i, item in enumerate(items, 1):
            target = item.get("path") or item.get("cleanup_command") or "N/A"
            # Shorten home path for display
            display_target = str(target).replace(str(Path.home()), "~")
            sz = item.get("size_human", "0 B")

            if item.get("is_protected"):
                status = "[red]BLOCKED[/red]"
            elif not item.get("is_safe", True):
                status = "[yellow]CAUTION[/yellow]"
            else:
                status = "[green]SAFE[/green]"
                total_executable_bytes += item.get("size_bytes", 0)
                if item.get("cleanup_command"):
                    cleanup_cmds.append(item["cleanup_command"])
                elif item.get("path"):
                    deletable_paths.append(Path(item["path"]))

            # Category and target come from the model: escape so they can't inject Rich markup
            table.add_row(str(i), escape(str(item.get("category", "General"))), escape(display_target), sz, status)

        self.console.print()
        self.console.print(table)

        if blocked > 0:
            self.console.print(
                f"[yellow]Note: {blocked} item(s) were automatically BLOCKED by safety guardrails.[/yellow]"
            )

        if not deletable_paths and not cleanup_cmds:
            self.console.print("[dim]No actionable items found in this plan.[/dim]\n")
            return False

        dest_label = "external backup drive" if self.use_backup else "System Trash / Recycle Bin"
        self.console.print(f"\n[bold]Target destination:[/bold] [cyan]{dest_label}[/cyan]")
        self.console.print(f"[bold]Reclaimable storage:[/bold] [yellow]{format_size(total_executable_bytes)}[/yellow]\n")

        if not Confirm.ask("Proceed with this cleanup plan?", default=False):
            self.console.print("[dim]Cleanup plan cancelled by user. No files were touched.[/dim]\n")
            return False

        # Execute approved plan
        backup_dir = None
        if self.use_backup:
            drive = select_external_drive(self.console, needed_bytes=total_executable_bytes)
            if drive:
                backup_dir = get_backup_dir(drive)

        self.console.print("\n[bold cyan]Executing cleanup...[/bold cyan]")

        # 1. Run package manager clean commands
        for cmd in cleanup_cmds:
            self.console.print(f"  [cyan]Running:[/cyan] {cmd}")
            run_cleanup_command(cmd, console=self.console)

        # 2. Delete / Backup files
        if deletable_paths:
            count, freed = remove_files(
                deletable_paths,
                backup_dir=backup_dir,
                console=self.console,
            )
            self.console.print(
                f"\n[bold green]Success![/bold green] Cleaned {count} items, freed {format_size(freed)}.\n"
            )

        if backup_dir:
            _offer_drive_eject(backup_dir, self.console)

        return True

    def run_turn(self, user_input: str) -> str:
        """Run a single conversational turn with multi-step tool execution."""
        self.conversation_history.append(ChatMessage(role="user", content=user_input))

        turn = 0
        final_answer = ""

        while turn < self.max_turns:
            turn += 1

            with self.console.status("[cyan]TidyCopilot is thinking...[/cyan]", spinner="dots"):
                try:
                    response = self.provider.chat(
                        self.conversation_history,
                        tools=TOOLS_SCHEMA,
                    )
                except Exception as exc:
                    self.console.print(f"[red]Model Provider Error:[/red] {exc}")
                    return f"Error: {exc}"

            # Append assistant response to history
            self.conversation_history.append(
                ChatMessage(
                    role="assistant",
                    content=response.content,
                    tool_calls=response.tool_calls,
                )
            )

            # If no tools called, we're done
            if not response.tool_calls:
                final_answer = response.content
                break

            # Execute tool calls
            for tc in response.tool_calls:
                self.console.print(f"[dim]⚙️  TidyCopilot executing: [bold]{tc.name}[/bold]...[/dim]")

                tool_result = execute_tool(tc.name, tc.arguments)

                # Special interactive handling for proposed plans
                if tc.name == "propose_cleanup_plan":
                    executed = self._render_plan(tool_result)
                    tool_result["user_approved_and_executed"] = executed

                # Feed tool result back to agent
                self.conversation_history.append(
                    ChatMessage(
                        role="tool",
                        content=json.dumps(tool_result),
                        tool_call_id=tc.id,
                        name=tc.name,
                    )
                )

        if final_answer:
            self.console.print()
            self.console.print(Markdown(final_answer))
            self.console.print()

        return final_answer

    def interactive_chat(self) -> None:
        """Launch an interactive terminal REPL session."""
        active_name, reason = get_active_provider(self.cfg)
        model_name = getattr(self.provider, "model", "default")

        welcome_text = (
            f"**TidyCopilot Interactive AI Assistant**\n\n"
            f"- **Active Provider:** `{self.provider.name}` ({reason})\n"
            f"- **Model:** `{model_name}`\n"
            f"- **Safety Mode:** Tier 1 System Protected + Tier 2 Confirmation Required\n\n"
            f"Ask questions like:\n"
            f"- *'Free up 10GB of storage'* \n"
            f"- *'Find large files taking up space in Downloads'* \n"
            f"- *'Clean old caches and developer clutter'* \n"
            f"- *'Show my disk usage summary'* \n\n"
            f"Type `/exit` or `quit` to leave, or `/clear` to reset context."
        )

        self.console.print(
            Panel(
                Markdown(welcome_text),
                title="[bold cyan]tidymac copilot[/bold cyan]",
                border_style="cyan",
            )
        )

        while True:
            try:
                user_msg = Prompt.ask("\n[bold cyan]You[/bold cyan]").strip()
                if not user_msg:
                    continue

                if user_msg.lower() in ("/exit", "exit", "quit", ":q"):
                    self.console.print("[dim]Goodbye![/dim]")
                    break

                if user_msg.lower() in ("/clear", "clear"):
                    self.conversation_history = [
                        ChatMessage(role="system", content=SYSTEM_PROMPT)
                    ]
                    self.console.print("[dim]Conversation history cleared.[/dim]")
                    continue

                self.run_turn(user_msg)

            except (KeyboardInterrupt, EOFError):
                self.console.print("\n[dim]Session ended.[/dim]")
                break
