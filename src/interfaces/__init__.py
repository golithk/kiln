"""Abstract interfaces for ticket system integrations."""

from src.interfaces.ticket import (
    CheckRunResult,
    Comment,
    LinkedPullRequest,
    TicketClient,
    TicketItem,
)

__all__ = [
    "CheckRunResult",
    "Comment",
    "LinkedPullRequest",
    "TicketClient",
    "TicketItem",
]
