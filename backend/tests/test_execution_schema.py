"""Schema and claim CRUD regression tests (stdlib unittest)."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app import config, crud_claims, db
from app.schemas.metadata import NodeMetadataModel, NodeTaskMetadata, SubtaskItem


class MetadataSchemaTests(unittest.TestCase):
    def test_legacy_human_procedure_parses(self) -> None:
        payload = {
            "data_sources": [
                {
                    "source_name": "FNOL intake",
                    "human_procedure": "Review uploaded documents",
                }
            ],
            "output_end_product": "Dossier",
        }
        meta = NodeTaskMetadata.from_payload(payload)
        self.assertEqual(meta.data_sources[0].user_procedure, "Review uploaded documents")
        dumped = meta.model_dump()
        self.assertNotIn("human_procedure", dumped["data_sources"][0])

    def test_runtime_subtask_fields_persist(self) -> None:
        item = SubtaskItem.model_validate(
            {
                "subtask_id": "st-001",
                "source_name": "Policy DB",
                "execution_mode": "user_manual",
                "agent_endpoint_key": "policy-resolver",
                "input_parameter_mappings": {"claim_id": "input_parameter"},
                "artifact_path_pattern": "data/artifacts/{claim_id}/policy.json",
            }
        )
        self.assertEqual(item.execution_mode, "user_manual")
        self.assertEqual(item.input_parameter_mappings["claim_id"], "input_parameter")

    def test_minimal_legacy_payload(self) -> None:
        meta = NodeMetadataModel.from_payload({})
        self.assertEqual(meta.input_parameter, "")
        self.assertEqual(meta.data_sources, [])
        self.assertFalse(meta.user_validation_required)


class ClaimCrudTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.sqlite"
        self._prev_db_path = config.DB_PATH
        config.DB_PATH = self.db_path
        db.init_db()
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.process_id = "proc-test-1"
        self.conn.execute(
            "INSERT INTO process (id, process_name, filename, raw_bpmn_xml) "
            "VALUES (?, ?, ?, ?)",
            (self.process_id, "Test Process", "test.bpmn", "<bpmn/>"),
        )
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()
        config.DB_PATH = self._prev_db_path
        self._tmpdir.cleanup()

    def test_claim_and_subtask_lifecycle(self) -> None:
        claim = crud_claims.create_claim_instance(
            self.conn,
            self.process_id,
            "CLM-2026-8819",
            {"claim_id": "CLM-2026-8819", "document_path": "s3://bucket/doc.pdf"},
        )
        self.assertEqual(claim.status, "INITIATED")
        self.assertEqual(claim.claim_parameters["claim_id"], "CLM-2026-8819")

        execution = crud_claims.create_subtask_execution(
            self.conn,
            claim.id,
            "st-resolve-source",
            "Resolve source location",
        )
        self.assertEqual(execution.status, "PENDING")

        crud_claims.update_subtask_execution(
            self.conn,
            execution.id,
            "RUNNING",
            trace_id="trace-abc",
            session_id="session-xyz",
            artifact_path="data/artifacts/CLM-2026-8819/source.json",
            output_payload={"qualified_name": "s3a://demo/path"},
        )
        crud_claims.update_claim_status(self.conn, claim.id, "PROCESSING")
        self.conn.commit()

        refreshed_claim = crud_claims.get_claim_instance(self.conn, claim.id)
        self.assertEqual(refreshed_claim.status, "PROCESSING")

        row = self.conn.execute(
            "SELECT status, trace_id, output_payload_json FROM subtask_execution WHERE id = ?",
            (execution.id,),
        ).fetchone()
        self.assertEqual(row["status"], "RUNNING")
        self.assertEqual(row["trace_id"], "trace-abc")
        self.assertEqual(json.loads(row["output_payload_json"])["qualified_name"], "s3a://demo/path")

    def test_tables_exist(self) -> None:
        claim_schema = self.conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='claim_instance'"
        ).fetchone()
        subtask_schema = self.conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='subtask_execution'"
        ).fetchone()
        self.assertIsNotNone(claim_schema)
        self.assertIsNotNone(subtask_schema)
        self.assertIn("AWAITING_USER_VALIDATION", claim_schema["sql"])
        self.assertIn("AWAITING_USER_VALIDATION", subtask_schema["sql"])


if __name__ == "__main__":
    unittest.main()
