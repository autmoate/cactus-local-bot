from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.table import Table

from modules.needle_router import Proposal


def ask_approval(console: Console, proposal: Proposal) -> bool:
    table = Table(show_header=False, box=None, pad_edge=False, expand=True)
    table.add_column("Feld", style="cyan", width=11)
    table.add_column("Wert", overflow="fold")
    table.add_row("Tool", f"[bold]{proposal.tool}[/]")
    for key, value in (proposal.arguments or {}).items():
        shown = value if isinstance(value, (int, float)) and value is not None else str(value or "")
        table.add_row(key, shown if shown.strip() else "–")
    if proposal.confidence is not None:
        table.add_row("Konfidenz", f"{proposal.confidence}")
    console.print(Panel(table, title="Freigabe nötig", border_style="yellow"))
    return Confirm.ask("Ausführen?", default=False, console=console)
