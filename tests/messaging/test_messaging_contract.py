from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from uuid import uuid7

from orqetia.infrastructure.messaging import build_inbox_table, build_outbox_table
from orqetia.infrastructure.persistence.schemas import metadata_for_schema
from orqetia.shared.messaging import (
    DataClassification,
    EventEnvelope,
    MAX_INLINE_PAYLOAD_BYTES,
    QueueName,
    WorkItem,
)


class MessagingContractTests(unittest.TestCase):
    def test_secret_work_payload_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.work_item(data_classification=DataClassification.SECRET)

    def test_client_private_requires_tenant_and_client_scope(self) -> None:
        with self.assertRaises(ValueError):
            self.work_item(data_classification=DataClassification.CLIENT_PRIVATE)

        item = self.work_item(
            data_classification=DataClassification.CLIENT_PRIVATE,
            tenant_id=uuid7(),
            client_id=uuid7(),
        )
        self.assertEqual(item.data_classification, DataClassification.CLIENT_PRIVATE)

    def test_inline_payload_above_64_kib_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.work_item(payload={"data": "x" * (MAX_INLINE_PAYLOAD_BYTES + 1)})

    def test_priority_and_infrastructure_attempts_are_bounded(self) -> None:
        with self.assertRaises(ValueError):
            self.work_item(priority=101)
        with self.assertRaises(ValueError):
            self.work_item(max_infrastructure_attempts=0)

    def test_event_contract_rejects_secret_and_requires_dotted_name(self) -> None:
        with self.assertRaises(ValueError):
            EventEnvelope(
                event_id=uuid7(),
                event_type="invalid",
                event_version=1,
                producer="execution",
                occurred_at=datetime.now(UTC),
                aggregate_type="task",
                aggregate_id=uuid7(),
                data_classification=DataClassification.INTERNAL,
                payload={},
            )

        with self.assertRaises(ValueError):
            EventEnvelope(
                event_id=uuid7(),
                event_type="task.completed",
                event_version=1,
                producer="execution",
                occurred_at=datetime.now(UTC),
                aggregate_type="task",
                aggregate_id=uuid7(),
                data_classification=DataClassification.SECRET,
                payload={},
            )

    def test_outbox_and_inbox_belong_to_owner_metadata(self) -> None:
        execution = metadata_for_schema("execution")
        outbox = build_outbox_table(execution)
        inbox = build_inbox_table(execution)

        self.assertEqual(outbox.schema, "execution")
        self.assertEqual(inbox.schema, "execution")
        self.assertEqual(outbox.name, "outbox_events")
        self.assertEqual(inbox.name, "inbox_events")

    def test_outbox_inbox_cannot_be_owned_by_messaging_schema(self) -> None:
        with self.assertRaises(ValueError):
            build_outbox_table(metadata_for_schema("messaging"))

    def test_work_contract_has_no_canonical_provider_retry_controls(self) -> None:
        item = self.work_item()
        fields = set(item.__dataclass_fields__)
        self.assertFalse({"max_cycles", "provider_retry_count", "provider_retry_after"} & fields)
        self.assertIn("max_infrastructure_attempts", fields)

    @staticmethod
    def work_item(**overrides: object) -> WorkItem:
        values: dict[str, object] = {
            "work_id": uuid7(),
            "queue_name": QueueName.EXECUTION,
            "operation_type": "execute_task",
            "operation_version": 1,
            "data_classification": DataClassification.INTERNAL,
            "payload": {"task_id": str(uuid7())},
            "available_at": datetime.now(UTC) + timedelta(seconds=1),
        }
        values.update(overrides)
        return WorkItem(**values)


if __name__ == "__main__":
    unittest.main()
