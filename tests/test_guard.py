import tempfile
import unittest
from pathlib import Path

from strictlyboolean_guard import (
    BraveBudgetExceeded,
    GuardConfig,
    RateLimitExceeded,
    StateStore,
)


class GuardTests(unittest.TestCase):
    def make_store(self, **overrides):
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        config = GuardConfig()
        config.brave_daily_limit = overrides.get("brave_daily_limit", 10)
        config.brave_monthly_limit = overrides.get("brave_monthly_limit", 20)
        config.rate_hourly_limit = overrides.get("rate_hourly_limit", 10)
        config.rate_daily_limit = overrides.get("rate_daily_limit", 20)
        config.cache_ttl_seconds = overrides.get("cache_ttl_seconds", 3600)
        path = str(Path(tempdir.name) / "state.sqlite3")
        return StateStore(path, config)

    def test_daily_brave_budget_is_hard(self):
        store = self.make_store(brave_daily_limit=2, brave_monthly_limit=10)
        store.reserve_brave_request()
        store.reserve_brave_request()
        with self.assertRaises(BraveBudgetExceeded) as ctx:
            store.reserve_brave_request()
        self.assertEqual(ctx.exception.period, "daily")
        usage = store.brave_usage()
        self.assertEqual(usage["daily_used"], 2)
        self.assertEqual(usage["monthly_used"], 2)

    def test_monthly_brave_budget_is_hard(self):
        store = self.make_store(brave_daily_limit=10, brave_monthly_limit=2)
        store.reserve_brave_request()
        store.reserve_brave_request()
        with self.assertRaises(BraveBudgetExceeded) as ctx:
            store.reserve_brave_request()
        self.assertEqual(ctx.exception.period, "monthly")

    def test_rate_limit_rejects_third_search(self):
        store = self.make_store(rate_hourly_limit=2, rate_daily_limit=20)
        store.check_rate_limit("127.0.0.1", now=10000)
        store.check_rate_limit("127.0.0.1", now=10001)
        with self.assertRaises(RateLimitExceeded):
            store.check_rate_limit("127.0.0.1", now=10002)

    def test_rate_limits_are_per_client(self):
        store = self.make_store(rate_hourly_limit=1, rate_daily_limit=20)
        store.check_rate_limit("client-a", now=10000)
        store.check_rate_limit("client-b", now=10000)
        with self.assertRaises(RateLimitExceeded):
            store.check_rate_limit("client-a", now=10001)

    def test_completed_search_cache_round_trip_and_expiry(self):
        store = self.make_store(cache_ttl_seconds=60)
        result = {"query": "cats AND dogs", "counts": {"matches": 3}}
        store.set_cached_search("cats AND dogs", "", "", result, now=1000)

        cached = store.get_cached_search("cats AND dogs", "", "", now=1010)
        self.assertEqual(cached["value"], result)
        self.assertEqual(cached["age_seconds"], 10)

        expired = store.get_cached_search("cats AND dogs", "", "", now=1061)
        self.assertIsNone(expired)

    def test_cache_key_includes_site_facets(self):
        store = self.make_store()
        left = store.cache_key("physics", "edu", "")
        right = store.cache_key("physics", "gov", "")
        self.assertNotEqual(left, right)


if __name__ == "__main__":
    unittest.main()
