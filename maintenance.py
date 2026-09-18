"""Explicit, environment-gated production maintenance operations."""
import os
import re
import sqlite3
import time
from pathlib import Path


def reset_order_data_once(connection, db_path, reset_id):
    """Back up the database, then remove orders and only order-bound jobs once."""
    reset_id = str(reset_id or "").strip()
    if not reset_id:
        return None
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", reset_id):
        raise ValueError("invalid_order_data_reset_id")

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS maintenance_migrations (
            migration_id TEXT PRIMARY KEY,
            applied_at INTEGER NOT NULL,
            detail TEXT NOT NULL DEFAULT ''
        )
        """
    )
    connection.commit()
    if connection.execute(
        "SELECT 1 FROM maintenance_migrations WHERE migration_id=?", (reset_id,)
    ).fetchone():
        return None

    source_path = Path(db_path).resolve()
    backup_path = source_path.with_name(
        f"{source_path.name}.before-{reset_id}-{int(time.time())}.sqlite3"
    )
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(backup_path)) as backup:
        connection.backup(backup)
    try:
        os.chmod(backup_path, 0o600)
    except OSError:
        pass

    connection.execute("BEGIN IMMEDIATE")
    try:
        order_count = connection.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        reminder_count = connection.execute("SELECT COUNT(*) FROM reminder_jobs").fetchone()[0]
        order_job_count = connection.execute(
            "SELECT COUNT(*) FROM engagement_jobs WHERE target_type IN ('order','partner_sale')"
        ).fetchone()[0]
        connection.execute("DELETE FROM reminder_jobs")
        connection.execute(
            "DELETE FROM engagement_jobs WHERE target_type IN ('order','partner_sale')"
        )
        connection.execute("DELETE FROM orders")
        detail = (
            f"orders={order_count};reminders={reminder_count};"
            f"order_jobs={order_job_count};backup={backup_path.name}"
        )
        connection.execute(
            "INSERT INTO maintenance_migrations (migration_id, applied_at, detail) VALUES (?, ?, ?)",
            (reset_id, int(time.time()), detail),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise

    return {
        "orders": order_count,
        "reminders": reminder_count,
        "order_jobs": order_job_count,
        "backup": backup_path.name,
    }
