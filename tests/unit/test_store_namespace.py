"""Tests for FundamentalsStore namespace support."""

from unittest.mock import MagicMock, patch

from tokenomics.fundamentals.store import FundamentalsStore


class TestStoreNamespace:
    @patch("tokenomics.fundamentals.store.redis.Redis")
    def test_default_namespace(self, mock_redis_cls):
        """No namespace -> default 'fundamentals' prefix."""
        store = FundamentalsStore()
        assert store.KEY_PREFIX == "fundamentals"
        assert store.SCORES_KEY == "fundamentals:scores"

    @patch("tokenomics.fundamentals.store.redis.Redis")
    def test_custom_namespace(self, mock_redis_cls):
        """Custom namespace overrides KEY_PREFIX and SCORES_KEY."""
        store = FundamentalsStore(namespace="fundamentals:v2_base")
        assert store.KEY_PREFIX == "fundamentals:v2_base"
        assert store.SCORES_KEY == "fundamentals:v2_base:scores"

    @patch("tokenomics.fundamentals.store.redis.Redis")
    def test_universe_keys_always_shared(self, mock_redis_cls):
        """UNIVERSE_KEY and UNIVERSE_MARKETCAP_KEY are class-level and never namespaced."""
        store = FundamentalsStore(namespace="fundamentals:v3_comp")
        assert store.UNIVERSE_KEY == "fundamentals:universe"
        assert store.UNIVERSE_MARKETCAP_KEY == "fundamentals:universe:marketcap"

    @patch("tokenomics.fundamentals.store.redis.Redis")
    def test_none_namespace_uses_defaults(self, mock_redis_cls):
        """Explicit None namespace behaves like no arg."""
        store = FundamentalsStore(namespace=None)
        assert store.KEY_PREFIX == "fundamentals"
        assert store.SCORES_KEY == "fundamentals:scores"

    @patch("tokenomics.fundamentals.store.redis.Redis")
    def test_namespaced_key_used_in_is_fresh(self, mock_redis_cls):
        """is_fresh should use namespaced key prefix."""
        mock_client = MagicMock()
        mock_redis_cls.return_value = mock_client
        mock_client.hget.return_value = None

        store = FundamentalsStore(namespace="fundamentals:v2_base")
        store.is_fresh("AAPL")

        mock_client.hget.assert_called_once_with("fundamentals:v2_base:AAPL", "updated")

    @patch("tokenomics.fundamentals.store.redis.Redis")
    def test_namespaced_key_used_in_get_company(self, mock_redis_cls):
        """get_company should use namespaced key prefix."""
        mock_client = MagicMock()
        mock_redis_cls.return_value = mock_client
        mock_client.hgetall.return_value = {}

        store = FundamentalsStore(namespace="fundamentals:v3_composite")
        store.get_company("MSFT")

        mock_client.hgetall.assert_called_once_with("fundamentals:v3_composite:MSFT")

    @patch("tokenomics.fundamentals.store.redis.Redis")
    def test_namespaced_scores_key_in_get_top(self, mock_redis_cls):
        """get_top_scores should use namespaced scores key."""
        mock_client = MagicMock()
        mock_redis_cls.return_value = mock_client
        mock_client.zrevrange.return_value = []

        store = FundamentalsStore(namespace="fundamentals:v2_base")
        store.get_top_scores(10)

        mock_client.zrevrange.assert_called_once_with("fundamentals:v2_base:scores", 0, 9, withscores=True)


class TestStoreSectors:
    @patch("tokenomics.fundamentals.store.redis.Redis")
    def test_save_universe_with_sectors(self, mock_redis_cls):
        """save_universe stores sector hash when sectors provided."""
        mock_client = MagicMock()
        mock_redis_cls.return_value = mock_client
        mock_pipeline = MagicMock()
        mock_client.pipeline.return_value = mock_pipeline

        store = FundamentalsStore()
        sectors = {"AAPL": "Technology", "XOM": "Energy", "JPM": "Financial Services"}
        store.save_universe(
            [("AAPL", 3000.0), ("XOM", 500.0), ("JPM", 400.0)],
            sectors=sectors,
        )

        # Check that hset was called with sectors
        hset_calls = mock_pipeline.hset.call_args_list
        sector_call = [c for c in hset_calls if c.kwargs.get("mapping") == sectors]
        assert len(sector_call) == 1

        # Check delete was called for sectors key
        delete_calls = [c for c in mock_pipeline.delete.call_args_list]
        sector_deletes = [c for c in delete_calls if "fundamentals:universe:sectors" in c.args]
        assert len(sector_deletes) == 1

    @patch("tokenomics.fundamentals.store.redis.Redis")
    def test_save_universe_without_sectors(self, mock_redis_cls):
        """save_universe skips sector hash when sectors not provided."""
        mock_client = MagicMock()
        mock_redis_cls.return_value = mock_client
        mock_pipeline = MagicMock()
        mock_client.pipeline.return_value = mock_pipeline

        store = FundamentalsStore()
        store.save_universe([("AAPL", 3000.0)])

        # Should not delete or hset the sectors key
        delete_calls = [c for c in mock_pipeline.delete.call_args_list]
        sector_deletes = [c for c in delete_calls if "fundamentals:universe:sectors" in c.args]
        assert len(sector_deletes) == 0

    @patch("tokenomics.fundamentals.store.redis.Redis")
    def test_get_sectors(self, mock_redis_cls):
        """get_sectors returns sector mapping from Redis."""
        mock_client = MagicMock()
        mock_redis_cls.return_value = mock_client
        mock_client.hgetall.return_value = {"AAPL": "Technology", "XOM": "Energy"}

        store = FundamentalsStore()
        result = store.get_sectors()

        assert result == {"AAPL": "Technology", "XOM": "Energy"}
        mock_client.hgetall.assert_called_with("fundamentals:universe:sectors")

    @patch("tokenomics.fundamentals.store.redis.Redis")
    def test_get_sectors_empty(self, mock_redis_cls):
        """get_sectors returns empty dict when no sectors stored."""
        mock_client = MagicMock()
        mock_redis_cls.return_value = mock_client
        mock_client.hgetall.return_value = {}

        store = FundamentalsStore()
        result = store.get_sectors()

        assert result == {}

    @patch("tokenomics.fundamentals.store.redis.Redis")
    def test_get_sector_single(self, mock_redis_cls):
        """get_sector returns sector for a single symbol."""
        mock_client = MagicMock()
        mock_redis_cls.return_value = mock_client
        mock_client.hget.return_value = "Technology"

        store = FundamentalsStore()
        result = store.get_sector("AAPL")

        assert result == "Technology"
        mock_client.hget.assert_called_with("fundamentals:universe:sectors", "AAPL")

    @patch("tokenomics.fundamentals.store.redis.Redis")
    def test_get_sector_not_found(self, mock_redis_cls):
        """get_sector returns None for unknown symbol."""
        mock_client = MagicMock()
        mock_redis_cls.return_value = mock_client
        mock_client.hget.return_value = None

        store = FundamentalsStore()
        result = store.get_sector("UNKNOWN")

        assert result is None
