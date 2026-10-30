"""
puppeteer_code/assistant/terminal.py
====================================
`pcode`: a chat in the current folder. It only reads the session's event stream,
so anything it shows is also available to the IDE.
"""

from __future__ import annotations

import getpass
import sys
import time
from pathlib import Path
from typing import Callable, Optional

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.markup import escape

from puppeteer_code.assistant.session import AssistantSession
from puppeteer_code.assistant.setup import KEY_HELP, load_keys, pick_provider, save_key

MAX_OUTPUT_LINES = 12
ACTIONS = {"created": "[green]+[/green]", "edited": "[yellow]~[/yellow]", "deleted": "[red]-[/red]"}

HELP = """[bold]Just type what you want[/bold], in plain words.

  /undo            put back what the last request changed
  /plan            show the current plan
  /mode ask|auto   ask before commands, or do not
  /model           show the models in use
  /model fast ID   change a model (or: /model quality ID)
  /usage           tokens used so far
  /clear           start a new conversation
  /resume [n]      list earlier conversations here, or continue one
  /help            this list
  /quit            leave"""


def _prompt_reader() -> Callable[[], str]:
    if not sys.stdin.isatty():
        return lambda: input("> ")
    try:
        from prompt_toolkit import PromptSession
    except ImportError:
        return lambda: input("> ")
    prompt = PromptSession()
    return lambda: prompt.prompt("> ")


class Terminal:
    def __init__(self, session: AssistantSession, console: Optional[Console] = None,
                 read: Optional[Callable[[], str]] = None,
                 confirm: Optional[Callable[[str], str]] = None):
        self.session = session
        self.console = console or Console()
        self.read = read or _prompt_reader()
        self.confirm = confirm or (lambda text: input(text))
        self.plan: list[str] = []
        self._output_lines = 0
        self._live: Optional[Live] = None
        self._reply = ""

    def run(self) -> int:
        self.banner()
        while True:
            try:
                line = self.read().strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not line:
                continue
            if line.startswith("/"):
                if not self.command(line):
                    break
                continue
            self.request(line)
        self.session.close()
        return 0

    def banner(self) -> None:
        session = self.session
        tiers = session.provider.tiers
        self.console.print(f"[bold]Puppeteer-Code[/bold]  {escape(str(session.project))}")
        self.console.print(f"[dim]{session.provider.provider}: fast {tiers.get('fast')}, "
                           f"quality {tiers.get('quality')}. Mode {session.tools.mode}. /help for commands.[/dim]")

    def request(self, message: str) -> None:
        stream = self.session.send(message)
        while True:
            try:
                event = next(stream)
                self.render(event)
            except StopIteration:
                return
            except KeyboardInterrupt:
                self.console.print("[yellow]Stopping...[/yellow]")
                self.session.cancel()

    # ── events ───────────────────────────────────────────────────────────────

    def render(self, event: dict) -> None:
        if event["type"] != "text_delta":
            self._end_reply()
        handler = getattr(self, f"_on_{event['type']}", None)
        if handler:
            handler(event)

    def _end_reply(self) -> None:
        if self._live:
            self._live.stop()
        self._live, self._reply = None, ""

    def _on_text_delta(self, event: dict) -> None:
        if self._live is None:
            self._live = Live(console=self.console, auto_refresh=False, vertical_overflow="visible")
            self._live.start()
        self._reply += event["text"]
        self._live.update(Markdown(self._reply), refresh=True)

    def _on_notice(self, event: dict) -> None:
        self.console.print(f"[yellow]{escape(event['text'])}[/yellow]")

    def _on_plan(self, event: dict) -> None:
        self.plan = event["steps"]
        self.console.print("[bold]Plan[/bold]")
        for i, step in enumerate(self.plan, 1):
            self.console.print(f"  {i}. {escape(step)}")

    def _on_agent(self, event: dict) -> None:
        self.console.print(f"[dim]{escape(event.get('label') or event['agent'])}...[/dim]")

    def _on_tool_start(self, event: dict) -> None:
        self._output_lines = 0
        args = event["args"]
        detail = args.get("path") or args.get("command") or args.get("pattern") or ""
        self.console.print(f"[dim]  {escape(event['tool'])} {escape(str(detail))}[/dim]")

    def _on_tool_end(self, event: dict) -> None:
        if not event["ok"]:
            self.console.print(f"[red]  {escape(event['summary'].splitlines()[0])}[/red]")

    def _on_file_changed(self, event: dict) -> None:
        self.console.print(f"  {ACTIONS[event['action']]} {escape(event['path'])}")

    def _on_command_output(self, event: dict) -> None:
        self._output_lines += 1
        if self._output_lines <= MAX_OUTPUT_LINES:
            self.console.print(f"[dim]  | {escape(event['text'].rstrip())}[/dim]")
        elif self._output_lines == MAX_OUTPUT_LINES + 1:
            self.console.print("[dim]  | ...[/dim]")

    def _on_preview(self, event: dict) -> None:
        self.console.print(f"[bold]Running at {escape(event['url'])}[/bold]")

    def _on_verify(self, event: dict) -> None:
        colour = "green" if event["ok"] else "red"
        self.console.print(f"[{colour}]  {escape(event['summary'])}[/{colour}]")

    def _on_rollback(self, event: dict) -> None:
        self.console.print(f"[yellow]  Went back: {escape(event['reason'])}[/yellow]")

    def _on_approval_request(self, event: dict) -> None:
        self.console.print(f"[yellow]Run this command?[/yellow] {escape(event['command'])}")
        try:
            answer = self.confirm("[y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        self.session.answer(event["id"], answer in ("y", "yes"))

    def _on_error(self, event: dict) -> None:
        self.console.print(f"[red]{escape(event['message'])}[/red]")

    def _on_done(self, event: dict) -> None:
        if event["status"] in ("stopped", "cancelled", "failed"):
            self.console.print(f"[yellow]{escape(event['reason'])}[/yellow]")
        count = len(event["files"])
        parts = [f"{self.session._spent():,} tokens"]
        if count:
            parts.insert(0, f"{count} file{'' if count == 1 else 's'} changed")
            parts.append("/undo to put them back")
        self.console.print(f"[dim]{'  |  '.join(parts)}[/dim]")

    # ── slash commands ───────────────────────────────────────────────────────

    def command(self, line: str) -> bool:
        name, *args = line[1:].split()
        handler = getattr(self, f"_cmd_{name}", None)
        if name in ("quit", "exit", "q"):
            return False
        if handler is None:
            self.console.print(f"[red]Unknown command /{escape(name)}.[/red] Try /help.")
            return True
        handler(args)
        return True

    def _cmd_help(self, args) -> None:
        self.console.print(HELP)

    def _cmd_undo(self, args) -> None:
        result = self.session.undo()
        if not result["restored"] and not result["kept"]:
            self.console.print("Nothing to undo.")
        for path in result["restored"]:
            self.console.print(f"  put back {escape(path)}")
        for path in result["kept"]:
            self.console.print(f"[yellow]  kept {escape(path)}: you changed it afterwards[/yellow]")

    def _cmd_plan(self, args) -> None:
        if not self.plan:
            self.console.print("No plan yet.")
        for i, step in enumerate(self.plan, 1):
            self.console.print(f"  {i}. {escape(step)}")

    def _cmd_mode(self, args) -> None:
        if args:
            try:
                self.session.set_mode(args[0])
            except ValueError as e:
                self.console.print(f"[red]{e}[/red]")
        self.console.print(f"Mode: {self.session.tools.mode}")

    def _cmd_model(self, args) -> None:
        tiers = self.session.provider.tiers
        if len(args) == 2 and args[0] in tiers:
            tiers[args[0]] = args[1]
        for tier, model in tiers.items():
            self.console.print(f"  {tier}: {escape(model)}")

    def _cmd_usage(self, args) -> None:
        self.console.print(f"{self.session.provider.meter.total_tokens:,} tokens this run")

    def _cmd_clear(self, args) -> None:
        self.session.history = []
        self.plan = []
        self.console.print("Started a new conversation. Undo still works.")

    def _cmd_resume(self, args) -> None:
        rows = AssistantSession.list_sessions(self.session.project, self.session.home)
        if not args:
            if not rows:
                self.console.print("No earlier conversations in this folder.")
            for i, row in enumerate(rows[:10], 1):
                when = time.strftime("%d %b %H:%M", time.localtime(row["saved"]))
                self.console.print(f"  {i}. {when}  {escape(row['title'])}")
            return
        try:
            row = rows[int(args[0]) - 1]
        except (ValueError, IndexError):
            self.console.print("[red]No such conversation.[/red]")
            return
        self.session.adopt(row["id"])
        self.console.print(f"Continuing: {escape(row['title'])}")


def _ask_for_key(home: Optional[Path]) -> Optional[str]:
    print("Puppeteer-Code needs an API key for an AI model, and none is set.")
    print(f"Groq has a free tier. Get a key at {KEY_HELP['groq']}")
    try:
        value = getpass.getpass("Paste your Groq key (it will not show), or press Enter to quit: ")
    except (EOFError, KeyboardInterrupt):
        return None
    if not value.strip():
        return None
    save_key("groq", value, home)
    print("Saved to your .pcode folder.")
    return "groq"


def run_chat(args) -> int:
    from puppeteer_code.providers import ProviderSession

    load_keys()
    provider = args.provider or pick_provider() or _ask_for_key(None)
    if provider is None:
        return 1
    models = ProviderSession(provider, cache_dir=args.cache)
    if args.resume:
        session = AssistantSession.resume(args.resume, models, mode=args.mode)
    else:
        session = AssistantSession(args.folder, models, mode=args.mode)
    return Terminal(session).run()
