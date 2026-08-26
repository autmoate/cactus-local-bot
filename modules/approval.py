from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.syntax import Syntax

from modules.needle_router import Proposal


def ask_approval(console: Console, proposal: Proposal) -> bool:
    body = {
        "tool": proposal.tool,
        "arguments": proposal.arguments,
        "confidence": proposal.confidence,
        "reasoning": proposal.reasoning,
    }
    console.print(Panel(Syntax(str(body), "python"), title="Needle Proposal"))
    return Confirm.ask("Execute this tool call?", default=False, console=console)
