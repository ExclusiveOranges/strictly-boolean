"""Persistent cost controls, rate limiting, and search-result caching.

The public beta should fail closed on API spending: every outbound Brave request
is reserved in SQLite before it is sent. The same small SQLite database also
stores per-IP search-rate events and cached completed searches.
"""

import hashlib
import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone


STATE_SCHEMA_VERSION = 1
CACHE_NAMESPACE = "search-v153"


class BraveBudgetExceeded(RuntimeError):
    def __init__(self, period, limit, used):
        self.period = period
        self.limit = int(limit)
        self.used = int(used)
        super().__init__(
            f"Global Brave {period} request budget reached "
            f"({self.used}/{self.limit})."
        )


class RateLimitExceeded(RuntimeError):
    def __init__(self, window, limit, retry_after_seconds):
        self.window = window
        self.limit = int(limit)
        self.retry_after_seconds = max(1, int(retry_after_seconds))
        super().__init__(
            f"Search rate limit reached for this address "
            f"({self.limit}/{self.window})."
        )


def _positive_int(value, default):
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return int(default)
    return parsed if parsed > 0 else int(default)


def _nonnegative_int(value, default):
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return int(default)
    return parsed if parsed >= 0 else int(default)


class GuardConfig:
    """Configuration loaded from environment variables.

    Defaults are deliberately finite. They are intended for a small public beta,
    not a high-traffic production deployment.
    """

    def __init__(self):
        self.brave_daily_limit = _positive_int(
            os.environ.get("SB_BRAVE_DAILY_LIMIT"), 500
        )
        self.brave_monthly_limit = _positive_int(
            os.environ.get("SB_BRAVE_MONTHLY_LIMIT"), 8000
        )
        self.rate_hourly_limit = _positive_int(
            os.environ.get("SB_RATE_LIMIT_HOURLY"), 60
        )
        self.rate_daily_limit = _positive_int(
            os.environ.get("SB_RATE_LIMIT_DAILY"), 200
        )
        self.cache_ttl_seconds = _nonnegative_int(
            os.environ.get("SB_SEARCH_CACHE_TTL_SECONDS"), 21600
        )
        self.trust_proxy = os.environ.get("SB_TRUST_PROXY", "0").strip().lower() in {
            "1", "true", "yes", "on"
        }
        self.state_db = os.environ.get(
            "SB_STATE_DB", "strictlyboolean_state.sqlite3"
        ).strip() or "strictlyboolean_state.sqlite3"


class StateStore:
    def __init__(self, path, config=None):
        self.path = path
        self.config = config or GuardConfig()
        self._init_lock = threading.Lock()
        self._initialized = False
        self._ensure_initialized()

    def _connect(self):
        connection = sqlite3.connect(
            self.path,
            timeout=10,
            isolation_level=None,
        )
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _ensure_initialized(self):
        if self._initialized:
            return
        with self._init_lock:
            if self._initialized:
                return
            connection = self._connect()
            try:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS meta (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS api_usage (
                        period_type TEXT NOT NULL,
                        period_key TEXT NOT NULL,
                        request_count INTEGER NOT NULL DEFAULT 0,
                        updated_at REAL NOT NULL,
                        PRIMARY KEY (period_type, period_key)
                    );

                    CREATE TABLE IF NOT EXISTS rate_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        client_key TEXT NOT NULL,
                        created_at REAL NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_rate_events_client_time
                        ON rate_events (client_key, created_at);

                    CREATE TABLE IF NOT EXISTS search_cache (
                        cache_key TEXT PRIMARY KEY,
                        created_at REAL NOT NULL,
                        expires_at REAL NOT NULL,
                        payload TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_search_cache_expires
                        ON search_cache (expires_at);
                    """
                )
                connection.execute(
                    "INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
                    ("schema_version", str(STATE_SCHEMA_VERSION)),
                )
            finally:
                connection.close()
            self._initialized = True

    @staticmethod
    def _period_keys(now=None):
        dt = now or datetime.now(timezone.utc)
        return dt.strftime("%Y-%m-%d"), dt.strftime("%Y-%m")

    def reserve_brave_request(self):
        """Atomically reserve one Brave API request before the network call."""
        daily_key, monthly_key = self._period_keys()
        now = time.time()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            rows = dict(
                connection.execute(
                    """
                    SELECT period_type, request_count
                    FROM api_usage
                    WHERE (period_type = 'day' AND period_key = ?)
                       OR (period_type = 'month' AND period_key = ?)
                    """,
                    (daily_key, monthly_key),
                ).fetchall()
            )
            daily_used = int(rows.get("day", 0))
            monthly_used = int(rows.get("month", 0))

            if daily_used >= self.config.brave_daily_limit:
                connection.execute("ROLLBACK")
                raise BraveBudgetExceeded(
                    "daily", self.config.brave_daily_limit, daily_used
                )
            if monthly_used >= self.config.brave_monthly_limit:
                connection.execute("ROLLBACK")
                raise BraveBudgetExceeded(
                    "monthly", self.config.brave_monthly_limit, monthly_used
                )

            connection.execute(
                """
                INSERT INTO api_usage(period_type, period_key, request_count, updated_at)
                VALUES('day', ?, 1, ?)
                ON CONFLICT(period_type, period_key)
                DO UPDATE SET request_count = request_count + 1, updated_at = excluded.updated_at
                """,
                (daily_key, now),
            )
            connection.execute(
                """
                INSERT INTO api_usage(period_type, period_key, request_count, updated_at)
                VALUES('month', ?, 1, ?)
                ON CONFLICT(period_type, period_key)
                DO UPDATE SET request_count = request_count + 1, updated_at = excluded.updated_at
                """,
                (monthly_key, now),
            )
            connection.execute("COMMIT")
        except (BraveBudgetExceeded, Exception):
            # BraveBudgetExceeded already rolled back above. For every other
            # exception, roll back only if a transaction is still active.
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def brave_usage(self):
        daily_key, monthly_key = self._period_keys()
        connection = self._connect()
        try:
            rows = dict(
                connection.execute(
                    """
                    SELECT period_type, request_count
                    FROM api_usage
                    WHERE (period_type = 'day' AND period_key = ?)
                       OR (period_type = 'month' AND period_key = ?)
                    """,
                    (daily_key, monthly_key),
                ).fetchall()
            )
        finally:
            connection.close()
        return {
            "daily_used": int(rows.get("day", 0)),
            "daily_limit": self.config.brave_daily_limit,
            "monthly_used": int(rows.get("month", 0)),
            "monthly_limit": self.config.brave_monthly_limit,
        }

    def check_rate_limit(self, client_key, now=None):
        """Count one search attempt and reject when an IP exceeds a window."""
        client_key = (client_key or "unknown").strip() or "unknown"
        now = float(time.time() if now is None else now)
        hour_start = now - 3600
        day_start = now - 86400

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            # Opportunistic cleanup keeps this table small.
            connection.execute(
                "DELETE FROM rate_events WHERE created_at < ?",
                (day_start - 60,),
            )

            hour_rows = connection.execute(
                """
                SELECT created_at FROM rate_events
                WHERE client_key = ? AND created_at >= ?
                ORDER BY created_at ASC
                """,
                (client_key, hour_start),
            ).fetchall()
            day_rows = connection.execute(
                """
                SELECT created_at FROM rate_events
                WHERE client_key = ? AND created_at >= ?
                ORDER BY created_at ASC
                """,
                (client_key, day_start),
            ).fetchall()

            if len(hour_rows) >= self.config.rate_hourly_limit:
                retry = (float(hour_rows[0][0]) + 3600) - now
                connection.execute("ROLLBACK")
                raise RateLimitExceeded(
                    "hour", self.config.rate_hourly_limit, retry
                )
            if len(day_rows) >= self.config.rate_daily_limit:
                retry = (float(day_rows[0][0]) + 86400) - now
                connection.execute("ROLLBACK")
                raise RateLimitExceeded(
                    "day", self.config.rate_daily_limit, retry
                )

            connection.execute(
                "INSERT INTO rate_events(client_key, created_at) VALUES(?, ?)",
                (client_key, now),
            )
            connection.execute("COMMIT")
        except (RateLimitExceeded, Exception):
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    @staticmethod
    def cache_key(query, include_site="", exclude_site=""):
        material = json.dumps(
            {
                "namespace": CACHE_NAMESPACE,
                "query": query,
                "include_site": include_site,
                "exclude_site": exclude_site,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(material).hexdigest()

    def get_cached_search(self, query, include_site="", exclude_site="", now=None):
        if self.config.cache_ttl_seconds <= 0:
            return None
        now = float(time.time() if now is None else now)
        key = self.cache_key(query, include_site, exclude_site)
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT created_at, expires_at, payload
                FROM search_cache
                WHERE cache_key = ?
                """,
                (key,),
            ).fetchone()
            if not row:
                return None
            created_at, expires_at, payload = row
            if float(expires_at) <= now:
                connection.execute(
                    "DELETE FROM search_cache WHERE cache_key = ?",
                    (key,),
                )
                return None
            try:
                value = json.loads(payload)
            except (TypeError, ValueError, json.JSONDecodeError):
                connection.execute(
                    "DELETE FROM search_cache WHERE cache_key = ?",
                    (key,),
                )
                return None
            return {
                "value": value,
                "created_at": float(created_at),
                "age_seconds": max(0, int(now - float(created_at))),
            }
        finally:
            connection.close()

    def set_cached_search(self, query, include_site, exclude_site, value, now=None):
        if self.config.cache_ttl_seconds <= 0:
            return
        now = float(time.time() if now is None else now)
        expires_at = now + self.config.cache_ttl_seconds
        key = self.cache_key(query, include_site, exclude_site)
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        connection = self._connect()
        try:
            connection.execute(
                """
                INSERT INTO search_cache(cache_key, created_at, expires_at, payload)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(cache_key)
                DO UPDATE SET
                    created_at = excluded.created_at,
                    expires_at = excluded.expires_at,
                    payload = excluded.payload
                """,
                (key, now, expires_at, payload),
            )
            connection.execute(
                "DELETE FROM search_cache WHERE expires_at <= ?",
                (now,),
            )
        finally:
            connection.close()
