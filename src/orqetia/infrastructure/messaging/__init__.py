"""PostgreSQL-backed messaging adapters."""

from .outbox import build_inbox_table, build_outbox_table
from .postgres import PostgresEventTransport, PostgresWakeup, PostgresWorkQueue
from .tables import event_deliveries, messaging_metadata, work_items

__all__ = [
    "PostgresEventTransport",
    "PostgresWakeup",
    "PostgresWorkQueue",
    "build_inbox_table",
    "build_outbox_table",
    "event_deliveries",
    "messaging_metadata",
    "work_items",
]
