"""Isolated incremental migration drill without Alembic env.py or live config."""

import importlib.util
import io
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory

from app.plugin.module_ai.agent.model import AgentEventModel, AgentRunModel
from app.plugin.module_ai.chat.model import MemoryExtractionJobModel
from app.plugin.module_ai.chat.usage_model import AiUsageDailyModel, AiUsageQuotaModel, AiUsageRecordModel
from app.plugin.module_ai.knowledge.model import KnowledgeBaseModel, KnowledgeDocumentModel
from app.plugin.module_ai.quality.model import AnswerFeedbackModel, RetrievalEvaluationModel

VERSIONS = Path(__file__).resolve().parents[1] / "app" / "alembic" / "versions"
REVISIONS = (
    "20261011_document_management",
    "20261011_ai_usage",
    "20261011_ai_quality",
    "20261022_memory_jobs",
    "20261022_memory_job_terminal",
)
NEW_DOCUMENT_COLUMNS = {"pending_file", "file_cleanup_paths"}
NEW_MODELS = (AiUsageQuotaModel, AiUsageDailyModel, AiUsageRecordModel, AnswerFeedbackModel, RetrievalEvaluationModel, MemoryExtractionJobModel)


def load_revision(name):
    spec = importlib.util.spec_from_file_location(f"isolated_{name}", VERSIONS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FeatureMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.engine = sa.create_engine(sa.URL.create("sqlite", database=str(Path(self.temporary.name) / "migration.db")))
        self.addCleanup(self.engine.dispose)
        self.migrations = [load_revision(name) for name in REVISIONS]
        # The existing user and knowledge relationship exists before the new revisions.
        metadata = sa.MetaData()
        sa.Table("sys_user", metadata, sa.Column("id", sa.Integer(), primary_key=True))
        KnowledgeBaseModel.__table__.to_metadata(metadata)
        columns = []
        for column in KnowledgeDocumentModel.__table__.columns:
            if column.name in NEW_DOCUMENT_COLUMNS:
                continue
            foreign_keys = [sa.ForeignKey(key.target_fullname, ondelete=key.ondelete, onupdate=key.onupdate) for key in column.foreign_keys]
            columns.append(sa.Column(column.name, column.type, *foreign_keys, primary_key=column.primary_key, nullable=column.nullable, default=column.default, server_default=column.server_default))
        document = sa.Table("ai_knowledge_document", metadata, *columns)
        with self.engine.begin() as connection:
            connection.execute(sa.text("PRAGMA foreign_keys=ON"))
            metadata.create_all(connection)
            connection.execute(metadata.tables["sys_user"].insert().values(id=1))
            connection.execute(KnowledgeBaseModel.__table__.insert().values(id=1, name="isolated", created_id=1))
            connection.execute(document.insert().values(id=1, knowledge_base_id=1, file_name="existing.txt", file_path="existing.txt", file_type="txt", file_size=3, created_id=1))

    def migrate(self, direction="upgrade"):
        with self.engine.begin() as connection:
            context = MigrationContext.configure(connection)
            migrations = self.migrations if direction == "upgrade" else reversed(self.migrations)
            for migration in migrations:
                with patch.object(migration, "op", Operations(context)):
                    getattr(migration, direction)()

    def snapshot(self):
        inspector = sa.inspect(self.engine)
        return {
            table: {
                "columns": [(column["name"], str(column["type"]), column["nullable"]) for column in inspector.get_columns(table)],
                "indexes": sorted((item["name"], tuple(item["column_names"]), item["unique"]) for item in inspector.get_indexes(table)),
            }
            for table in sorted(inspector.get_table_names())
        }

    def test_revision_chain_has_one_head(self):
        self.assertEqual([migration.revision for migration in self.migrations], list(REVISIONS))
        self.assertEqual([migration.down_revision for migration in self.migrations], ["20261004_demo_category", *REVISIONS[:-1]])
        config = Config()
        config.set_main_option("script_location", str(VERSIONS.parent))
        agent = load_revision("20261024_agent_runs")
        self.assertEqual(agent.down_revision, REVISIONS[-1])
        last_login = load_revision("20261114_last_login_naive")
        self.assertEqual(last_login.down_revision, agent.revision)
        self.assertEqual(ScriptDirectory.from_config(config).get_heads(), [last_login.revision])

    def test_agent_migration_matches_models_and_preserves_history(self):
        revision = load_revision("20261024_agent_runs")
        with self.engine.begin() as connection:
            context = MigrationContext.configure(connection)
            with patch.object(revision, "op", Operations(context)):
                revision.upgrade()
                revision.upgrade()
                with self.assertRaisesRegex(RuntimeError, "诊断任务"):
                    revision.downgrade()
        inspector = sa.inspect(self.engine)
        for model in (AgentRunModel, AgentEventModel):
            actual = {column["name"]: column for column in inspector.get_columns(model.__tablename__)}
            self.assertEqual(set(actual), set(model.__table__.columns.keys()))
            for column in model.__table__.columns:
                self.assertEqual(actual[column.name]["nullable"], column.nullable)
                self.assertEqual(actual[column.name]["type"]._type_affinity, column.type._type_affinity)
            self.assertEqual({item["name"] for item in inspector.get_indexes(model.__tablename__)},
                             {index.name for index in model.__table__.indexes})
        self.assertIn(("user_id", "request_id"),
                      [tuple(item["column_names"]) for item in inspector.get_unique_constraints("ai_agent_run")])
        self.assertEqual(inspector.get_pk_constraint("ai_agent_event")["constrained_columns"], ["run_id", "seq"])

    def test_upgrade_preserves_old_document_and_matches_new_models(self):
        self.migrate()
        inspector = sa.inspect(self.engine)
        columns = {column["name"]: column for column in inspector.get_columns("ai_knowledge_document")}
        for name in NEW_DOCUMENT_COLUMNS:
            self.assertIsInstance(columns[name]["type"], sa.JSON)
            self.assertTrue(columns[name]["nullable"])
        for model in NEW_MODELS:
            actual = {column["name"]: column for column in inspector.get_columns(model.__tablename__)}
            expected = {column.name: column for column in model.__table__.columns}
            self.assertEqual(set(actual), set(expected))
            for name, column in expected.items():
                self.assertEqual(actual[name]["nullable"], column.nullable, f"{model.__tablename__}.{name}")
                self.assertEqual(actual[name]["type"]._type_affinity, column.type._type_affinity, f"{model.__tablename__}.{name}")
            self.assertEqual({item["name"] for item in inspector.get_indexes(model.__tablename__)}, {index.name for index in model.__table__.indexes})
        constraints = inspector.get_unique_constraints("ai_answer_feedback")
        self.assertIn(("user_id", "session_id", "run_id"), [tuple(item["column_names"]) for item in constraints])
        self.assertEqual(inspector.get_pk_constraint("ai_usage_daily")["constrained_columns"], ["user_id", "day"])
        self.assertEqual(inspector.get_check_constraints("ai_usage_quota")[0]["name"], "ck_ai_usage_quota_limit")
        with self.engine.connect() as connection:
            row = connection.execute(sa.text("SELECT file_name,pending_file,file_cleanup_paths FROM ai_knowledge_document WHERE id=1")).one()
            self.assertEqual(tuple(row), ("existing.txt", None, None))
        first = self.snapshot()
        self.migrate()
        self.assertEqual(self.snapshot(), first)

    def test_migrated_tables_accept_current_model_payloads_and_constraints(self):
        self.migrate()
        today = date(2026, 10, 11)
        with self.engine.begin() as connection:
            connection.execute(sa.text("PRAGMA foreign_keys=ON"))
            connection.execute(
                KnowledgeDocumentModel.__table__.insert().values(
                    id=2,
                    knowledge_base_id=1,
                    file_name="new.txt",
                    file_type="txt",
                    file_size=8,
                    pending_file={"file_path": "replacement.txt", "file_size": 9},
                    file_cleanup_paths=["old.txt"],
                    created_id=1,
                )
            )
            connection.execute(AiUsageQuotaModel.__table__.insert().values(user_id=1, daily_limit=4))
            connection.execute(AiUsageDailyModel.__table__.insert().values(user_id=1, day=today, calls=1))
            connection.execute(
                AiUsageRecordModel.__table__.insert().values(
                    id=1,
                    user_id=1,
                    started_at=datetime(2026, 10, 11, 8),
                    day=today,
                    protocol="openai",
                    model="isolated",
                    purpose="chat",
                    status="success",
                    latency_ms=12,
                    input_tokens=5,
                    output_tokens=7,
                    total_tokens=12,
                )
            )
            connection.execute(
                AnswerFeedbackModel.__table__.insert().values(
                    id=1,
                    user_id=1,
                    session_id="session-1",
                    run_id="stable-run-1",
                    rating="incorrect",
                    comment="check",
                    question="q",
                    answer="a",
                    citations=[{"knowledge_base_id": 1, "document_id": 2}],
                    revision=2,
                )
            )
            connection.execute(
                RetrievalEvaluationModel.__table__.insert().values(
                    id=1,
                    user_id=1,
                    name="fixed questions",
                    dataset_hash="f" * 64,
                    knowledge_base_ids=[1],
                    top_k=5,
                    retrieval_mode="bm25",
                    dataset=[{"query": "q", "expected_document_ids": [2]}],
                    report={"recall": 1.0},
                )
            )
            self.assertEqual(
                connection.execute(sa.select(KnowledgeDocumentModel.__table__.c.pending_file).where(KnowledgeDocumentModel.__table__.c.id == 2)).scalar_one()["file_path"], "replacement.txt"
            )
            self.assertEqual(connection.execute(sa.select(AnswerFeedbackModel.__table__.c.revision)).scalar_one(), 2)
            self.assertEqual(connection.execute(sa.select(RetrievalEvaluationModel.__table__.c.report)).scalar_one(), {"recall": 1.0})
            with self.assertRaises(sa.exc.IntegrityError):
                connection.execute(AiUsageQuotaModel.__table__.insert().values(user_id=2, daily_limit=-1))
            with self.assertRaises(sa.exc.IntegrityError):
                connection.execute(AnswerFeedbackModel.__table__.insert().values(user_id=1, session_id="session-1", run_id="stable-run-1", rating="helpful", question="q", answer="a"))
            with self.assertRaises(sa.exc.IntegrityError):
                connection.execute(KnowledgeDocumentModel.__table__.insert().values(knowledge_base_id=999, file_name="bad.txt", file_type="txt"))

    def test_downgrade_only_drops_new_local_schema(self):
        self.migrate()
        self.migrate("downgrade")
        inspector = sa.inspect(self.engine)
        self.assertEqual(set(inspector.get_table_names()), {"sys_user", "ai_knowledge_base", "ai_knowledge_document"})
        self.assertFalse(NEW_DOCUMENT_COLUMNS.intersection(column["name"] for column in inspector.get_columns("ai_knowledge_document")))
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(sa.text("SELECT file_name FROM ai_knowledge_document WHERE id=1")).scalar_one(), "existing.txt")
        self.migrate("downgrade")
        self.migrate()
        self.assertIn("ai_answer_feedback", sa.inspect(self.engine).get_table_names())

    def test_mysql_migration_ddl_compiles_offline(self):
        buffer = io.StringIO()
        context = MigrationContext.configure(dialect_name="mysql", opts={"as_sql": True, "output_buffer": buffer})
        inspector = unittest.mock.Mock()
        inspector.get_table_names.return_value = ["sys_user", "ai_knowledge_document"]
        inspector.get_columns.return_value = [{"name": "id"}, {"name": "file_name"}]
        for migration in self.migrations:
            with patch.object(migration, "op", Operations(context)), patch.object(migration.sa, "inspect", return_value=inspector):
                migration.upgrade()
        ddl = buffer.getvalue()
        self.assertIn("ALTER TABLE ai_knowledge_document ADD COLUMN pending_file JSON", ddl)
        self.assertIn("CREATE TABLE ai_usage_record", ddl)
        self.assertIn("CREATE TABLE ai_answer_feedback", ddl)
        self.assertIn("CREATE TABLE ai_retrieval_evaluation", ddl)
        self.assertIn("uq_ai_answer_feedback", ddl)
        self.assertIn("revision INTEGER NOT NULL", ddl)
        self.assertIn("CREATE INDEX ix_ai_usage_record_day", ddl)
        self.assertNotIn("DROP TABLE", ddl)


if __name__ == "__main__":
    unittest.main()
