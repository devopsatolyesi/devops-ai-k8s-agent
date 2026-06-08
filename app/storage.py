from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.models import Finding, utc_now

logger = logging.getLogger(__name__)

# Increment this whenever the schema changes.
_SCHEMA_VERSION = 2


class Storage:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        # WAL mode: allows concurrent reads while a write is in progress.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    # ── Schema setup & migrations ──────────────────────────────────────────

    def _init_db(self) -> None:
        with self._connect() as conn:
            # Schema version tracking
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS db_meta (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )

            # Core findings table
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS findings (
                    id             TEXT PRIMARY KEY,
                    fingerprint    TEXT UNIQUE NOT NULL,
                    payload        TEXT NOT NULL,
                    first_seen     TEXT NOT NULL,
                    last_seen      TEXT NOT NULL,
                    resolved       INTEGER NOT NULL DEFAULT 0,
                    resolved_at    TEXT,
                    finding_number INTEGER
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_findings_last_seen ON findings(last_seen DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_findings_resolved ON findings(resolved, resolved_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_findings_fingerprint ON findings(fingerprint)"
            )

            # Runtime settings (persisted across restarts)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )

            # Crash event history for trend analysis
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS crash_history (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    fingerprint  TEXT NOT NULL,
                    namespace    TEXT NOT NULL,
                    pod_name     TEXT NOT NULL,
                    problem_type TEXT NOT NULL,
                    restart_count INTEGER NOT NULL DEFAULT 0,
                    recorded_at  TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_crash_fingerprint ON crash_history(fingerprint, recorded_at DESC)"
            )

            self._run_migrations(conn)

    def _run_migrations(self, conn: sqlite3.Connection) -> None:
        """Forward-only schema migrations. Add new entries as the schema evolves."""
        row = conn.execute("SELECT value FROM db_meta WHERE key='schema_version'").fetchone()
        current = int(row["value"]) if row else 0

        if current < 1:
            # v1: add resolved_at and finding_number columns if missing (legacy DBs)
            for col, typedef in [("resolved_at", "TEXT"), ("finding_number", "INTEGER")]:
                try:
                    conn.execute(f"ALTER TABLE findings ADD COLUMN {col} {typedef}")
                except sqlite3.OperationalError:
                    pass  # column already exists

        if current < 2:
            # v2: crash_history table (created above for new DBs; no-op for existing)
            pass

        conn.execute(
            "INSERT OR REPLACE INTO db_meta (key, value) VALUES ('schema_version', ?)",
            (str(_SCHEMA_VERSION),),
        )

    # ── Settings ───────────────────────────────────────────────────────────

    def save_setting(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )

    def load_settings(self) -> dict[str, str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {row["key"]: row["value"] for row in rows}

    # ── Finding CRUD ───────────────────────────────────────────────────────

    def _next_finding_number(self, conn: sqlite3.Connection) -> int:
        row = conn.execute("SELECT MAX(finding_number) AS mx FROM findings").fetchone()
        return (row["mx"] or 0) + 1

    def upsert_finding(self, finding: Finding) -> Finding:
        existing = self.get_by_fingerprint(finding.fingerprint)
        now = utc_now()
        if existing:
            # Preserve "remediating" status briefly during active fix application
            if existing.status == "remediating" and finding.status == "open" and not finding.resolved:
                started_at_str = existing.evidence.get("remediation_started_at")
                if started_at_str:
                    try:
                        started_at = datetime.fromisoformat(started_at_str)
                        if (datetime.now(UTC) - started_at).total_seconds() < 60:
                            finding.status = "remediating"
                            finding.resolved = False
                            finding.evidence["remediation_started_at"] = started_at_str
                    except Exception:
                        pass

            finding.id = existing.id
            finding.first_seen = existing.first_seen
            finding.ai_analysis = existing.ai_analysis or finding.ai_analysis
            finding.ai_used = existing.ai_used or finding.ai_used
            finding.ai_error = existing.ai_error or finding.ai_error
            finding.last_seen = now
            finding.finding_number = existing.finding_number
            # Preserve AI audit history
            finding.ai_history = existing.ai_history or finding.ai_history
            finding.last_restart_count_at_ai = existing.last_restart_count_at_ai

            if finding.resolved and not existing.resolved:
                finding.resolved_at = now
            elif finding.resolved and existing.resolved:
                finding.resolved_at = existing.resolved_at
            elif not finding.resolved:
                finding.resolved_at = None
        else:
            finding.last_seen = now

        if finding.resolved and finding.resolved_at is None:
            finding.resolved_at = now

        payload = finding.model_dump()
        with self._connect() as conn:
            if finding.finding_number is None:
                finding.finding_number = self._next_finding_number(conn)
                payload["finding_number"] = finding.finding_number

            conn.execute(
                """
                INSERT INTO findings (
                    id, fingerprint, payload, first_seen, last_seen,
                    resolved, resolved_at, finding_number
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(fingerprint) DO UPDATE SET
                    payload        = excluded.payload,
                    last_seen      = excluded.last_seen,
                    resolved       = excluded.resolved,
                    resolved_at    = excluded.resolved_at,
                    finding_number = COALESCE(findings.finding_number, excluded.finding_number)
                """,
                (
                    finding.id,
                    finding.fingerprint,
                    json.dumps(payload, default=str),
                    finding.first_seen,
                    finding.last_seen,
                    int(finding.resolved),
                    finding.resolved_at,
                    finding.finding_number,
                ),
            )
        return finding

    def update_ai_result(self, finding_id: str, ai_analysis: dict[str, Any] | None, ai_error: str | None) -> None:
        finding = self.get(finding_id)
        if not finding:
            return
        finding.ai_analysis = ai_analysis
        finding.ai_error = ai_error
        finding.ai_used = bool(ai_analysis)
        self.upsert_finding(finding)

    def get(self, finding_id: str) -> Finding | None:
        with self._connect() as conn:
            row = conn.execute("SELECT payload FROM findings WHERE id = ?", (finding_id,)).fetchone()
        return Finding(**json.loads(row["payload"])) if row else None

    def get_by_fingerprint(self, fingerprint: str) -> Finding | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM findings WHERE fingerprint = ?", (fingerprint,)
            ).fetchone()
        return Finding(**json.loads(row["payload"])) if row else None

    def list_findings(self) -> list[Finding]:
        """Active findings: unresolved items only, ordered by finding number."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM findings WHERE resolved = 0 ORDER BY finding_number ASC"
            ).fetchall()
        return [Finding(**json.loads(row["payload"])) for row in rows]

    def list_resolved(self) -> list[Finding]:
        """Remediating findings (in progress fixes) + properly resolved ones."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM findings WHERE json_extract(payload, '$.status') IN ('remediating', 'resolved_by_ai', 'resolved', 'resolved_manually') ORDER BY resolved_at DESC"
            ).fetchall()
        return [Finding(**json.loads(row["payload"])) for row in rows]

    def summary(
        self,
        ai_requests_total: int,
        ai_errors_total: int,
        last_scan_timestamp: float,
        duration: float,
    ) -> dict[str, Any]:
        findings = self.list_findings()
        active = [f for f in findings if not f.resolved]
        by_severity = {"Critical": 0, "High": 0, "Medium": 0, "Low": 0}
        by_namespace: dict[str, int] = {}
        for finding in active:
            by_severity[finding.severity] = by_severity.get(finding.severity, 0) + 1
            by_namespace[finding.namespace] = by_namespace.get(finding.namespace, 0) + 1
        return {
            "total_findings": len(active),
            "by_severity": by_severity,
            "by_namespace": by_namespace,
            "ai_requests_total": ai_requests_total,
            "ai_errors_total": ai_errors_total,
            "last_scan_timestamp": last_scan_timestamp,
            "last_scan_duration_seconds": duration,
        }

    # ── Crash history (trend analysis) ─────────────────────────────────────

    def record_crash_event(
        self,
        fingerprint: str,
        namespace: str,
        pod_name: str,
        problem_type: str,
        restart_count: int,
    ) -> None:
        """Record a crash event for trend tracking."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO crash_history (fingerprint, namespace, pod_name, problem_type, restart_count, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (fingerprint, namespace, pod_name, problem_type, restart_count, utc_now()),
            )

    def get_crash_trend(self, fingerprint: str, hours: int = 24) -> dict[str, Any]:
        """Return crash event count for a fingerprint within the last N hours."""
        cutoff = datetime.now(UTC).replace(microsecond=0)
        from datetime import timedelta
        cutoff_str = (cutoff - timedelta(hours=hours)).isoformat()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS event_count,
                       MAX(restart_count) AS max_restarts,
                       MIN(recorded_at) AS first_event
                FROM crash_history
                WHERE fingerprint = ? AND recorded_at >= ?
                """,
                (fingerprint, cutoff_str),
            ).fetchone()
        return {
            "event_count": row["event_count"] or 0,
            "max_restarts": row["max_restarts"] or 0,
            "first_event": row["first_event"],
            "window_hours": hours,
        }

    def get_top_crash_trends(self, limit: int = 5, hours: int = 24) -> list[dict[str, Any]]:
        """Return the top N most frequently crashing pods in the last N hours."""
        from datetime import timedelta
        cutoff_str = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT fingerprint, namespace, pod_name, problem_type,
                       COUNT(*) AS event_count,
                       MAX(restart_count) AS max_restarts
                FROM crash_history
                WHERE recorded_at >= ?
                GROUP BY fingerprint
                ORDER BY event_count DESC
                LIMIT ?
                """,
                (cutoff_str, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_remediated_fingerprints(self) -> set[str]:
        """Load the set of remediated fingerprints from SQLite settings."""
        try:
            settings_dict = self.load_settings()
            val = settings_dict.get("remediated_fingerprints", "[]")
            return set(json.loads(val))
        except Exception:
            return set()

    def add_remediated_fingerprint(self, fingerprint: str) -> None:
        """Add a fingerprint to the remediated list in SQLite settings."""
        try:
            fingerprints = self.get_remediated_fingerprints()
            fingerprints.add(fingerprint)
            self.save_setting("remediated_fingerprints", json.dumps(list(fingerprints)))
        except Exception as e:
            import logging
            logging.error("Failed to add remediated fingerprint: %s", e)

    def clear_remediated_fingerprints(self) -> None:
        """Clear all remediated fingerprints from SQLite settings."""
        try:
            self.save_setting("remediated_fingerprints", "[]")
        except Exception as e:
            import logging
            logging.error("Failed to clear remediated fingerprints: %s", e)

    def clear_findings(self) -> None:
        """Delete ALL findings from database (used by reset endpoint)."""
        try:
            with self._connect() as conn:
                conn.execute("DELETE FROM findings")
                conn.commit()
        except Exception as e:
            logger.error("Failed to clear findings: %s", e)

    def clear_findings_by_namespace(self, namespace: str) -> None:
        """Delete all findings in a specific namespace."""
        try:
            with self._connect() as conn:
                conn.execute(
                    "DELETE FROM findings WHERE json_extract(payload, '$.namespace') = ?",
                    (namespace,)
                )
                conn.commit()
        except Exception as e:
            logger.error("Failed to clear findings by namespace: %s", e)

    def clear_resolved_findings(self) -> None:
        """Delete only fake manually resolved findings (NOT AI-resolved ones)."""
        try:
            with self._connect() as conn:
                conn.execute(
                    "DELETE FROM findings WHERE json_extract(payload, '$.status') = 'resolved_manually'"
                )
                conn.commit()
        except Exception as e:
            logger.error("Failed to clear fake resolved findings: %s", e)
