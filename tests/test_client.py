"""Tests for IBKR client functionality."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from ib_async import Future, Stock, Order, Trade, OrderStatus

from ibkr_mcp_server.client import IBKRClient
from ibkr_mcp_server.utils import (
    ConnectionError as IBKRConnectionError,
    ValidationError,
    TradingError,
)


# ── Connection & Account Tests ────────────────────────────────────────


class TestConnection:

    def test_is_connected_true(self, ibkr_client):
        assert ibkr_client.is_connected() is True

    def test_is_connected_false_when_flag_off(self, ibkr_client):
        ibkr_client._connected = False
        assert ibkr_client.is_connected() is False

    def test_is_connected_false_when_no_ib(self, ibkr_client):
        ibkr_client.ib = None
        assert ibkr_client.is_connected() is False

    def test_is_paper_on_port_4002(self, ibkr_client):
        ibkr_client.port = 4002
        assert ibkr_client.is_paper is True

    def test_is_paper_on_port_7497(self, ibkr_client):
        ibkr_client.port = 7497
        assert ibkr_client.is_paper is True

    def test_not_paper_on_port_7496(self, ibkr_client):
        ibkr_client.port = 7496
        assert ibkr_client.is_paper is False


class TestAccountManagement:

    @pytest.mark.asyncio
    async def test_switch_account_valid(self, ibkr_client):
        result = await ibkr_client.switch_account("DU7654321")
        assert result["success"] is True
        assert ibkr_client.current_account == "DU7654321"

    @pytest.mark.asyncio
    async def test_switch_account_invalid(self, ibkr_client):
        result = await ibkr_client.switch_account("INVALID")
        assert result["success"] is False
        assert ibkr_client.current_account == "DU1234567"

    @pytest.mark.asyncio
    async def test_get_accounts(self, ibkr_client):
        accounts = await ibkr_client.get_accounts()
        assert accounts["current_account"] == "DU1234567"
        assert "DU1234567" in accounts["available_accounts"]
        assert "DU7654321" in accounts["available_accounts"]
        assert accounts["connected"] is True


# ── Contract Building Tests ───────────────────────────────────────────


class TestBuildContract:

    def test_build_futures_contract_with_local_symbol(self, ibkr_client):
        contract = ibkr_client._build_contract(
            symbol="ZR", sec_type="FUT", exchange="CBOT",
            local_symbol="ZRK6",
        )
        assert isinstance(contract, Future)
        assert contract.symbol == "ZR"
        assert contract.exchange == "CBOT"
        assert contract.localSymbol == "ZRK6"
        assert contract.currency == "USD"

    def test_build_futures_contract_with_expiry(self, ibkr_client):
        contract = ibkr_client._build_contract(
            symbol="ES", sec_type="FUT", exchange="CME",
            last_trade_date="20260620",
        )
        assert isinstance(contract, Future)
        assert contract.lastTradeDateOrContractMonth == "20260620"

    def test_build_stock_contract(self, ibkr_client):
        contract = ibkr_client._build_contract(
            symbol="AAPL", sec_type="STK", exchange="SMART",
        )
        assert isinstance(contract, Stock)
        assert contract.symbol == "AAPL"
        assert contract.exchange == "SMART"

    def test_build_stock_defaults_to_smart(self, ibkr_client):
        contract = ibkr_client._build_contract(
            symbol="AAPL", sec_type="STK", exchange="",
        )
        assert contract.exchange == "SMART"

    def test_local_symbol_takes_precedence_over_expiry(self, ibkr_client):
        contract = ibkr_client._build_contract(
            symbol="ZR", sec_type="FUT", exchange="CBOT",
            local_symbol="ZRK6", last_trade_date="20260514",
        )
        assert contract.localSymbol == "ZRK6"
        # expiry not set when local_symbol is provided
        assert contract.lastTradeDateOrContractMonth == ""


# ── Order Building Tests ──────────────────────────────────────────────


class TestBuildOrder:

    def test_market_order(self, ibkr_client):
        order = ibkr_client._build_order(
            action="BUY", quantity=1, order_type="MKT",
        )
        assert order.action == "BUY"
        assert order.totalQuantity == 1
        assert order.orderType == "MKT"
        assert order.tif == "GTC"

    def test_limit_order(self, ibkr_client):
        order = ibkr_client._build_order(
            action="SELL", quantity=2, order_type="LMT",
            limit_price=100.50,
        )
        assert order.orderType == "LMT"
        assert order.lmtPrice == 100.50

    def test_limit_order_requires_price(self, ibkr_client):
        with pytest.raises(ValidationError, match="limit_price required"):
            ibkr_client._build_order(
                action="BUY", quantity=1, order_type="LMT",
            )

    def test_stop_order(self, ibkr_client):
        order = ibkr_client._build_order(
            action="BUY", quantity=3, order_type="STP",
            stop_price=50.0,
        )
        assert order.orderType == "STP"
        assert order.auxPrice == 50.0

    def test_stop_order_requires_price(self, ibkr_client):
        with pytest.raises(ValidationError, match="stop_price required"):
            ibkr_client._build_order(
                action="BUY", quantity=1, order_type="STP",
            )

    def test_stop_limit_order(self, ibkr_client):
        order = ibkr_client._build_order(
            action="SELL", quantity=1, order_type="STP LMT",
            stop_price=99.0, limit_price=98.5,
        )
        assert order.orderType == "STP LMT"
        assert order.auxPrice == 99.0
        assert order.lmtPrice == 98.5

    def test_stop_limit_requires_both_prices(self, ibkr_client):
        with pytest.raises(ValidationError, match="Both stop_price and limit_price"):
            ibkr_client._build_order(
                action="BUY", quantity=1, order_type="STP LMT",
                stop_price=50.0,
            )

    def test_trailing_stop_with_amount(self, ibkr_client):
        order = ibkr_client._build_order(
            action="SELL", quantity=1, order_type="TRAIL",
            trail_amount=2.5, trail_stop_price=100.0,
        )
        assert order.orderType == "TRAIL"
        assert order.auxPrice == 2.5
        assert order.trailStopPrice == 100.0

    def test_trailing_stop_with_percent(self, ibkr_client):
        order = ibkr_client._build_order(
            action="SELL", quantity=1, order_type="TRAIL",
            trail_percent=5.0,
        )
        assert order.orderType == "TRAIL"
        assert order.trailingPercent == 5.0

    def test_trailing_stop_requires_amount_or_percent(self, ibkr_client):
        with pytest.raises(ValidationError, match="trail_amount or trail_percent"):
            ibkr_client._build_order(
                action="BUY", quantity=1, order_type="TRAIL",
            )

    def test_unsupported_order_type(self, ibkr_client):
        with pytest.raises(ValidationError, match="Unsupported order type"):
            ibkr_client._build_order(
                action="BUY", quantity=1, order_type="WEIRD",
            )

    def test_default_tif_is_gtc(self, ibkr_client):
        order = ibkr_client._build_order(
            action="BUY", quantity=1, order_type="MKT",
        )
        assert order.tif == "GTC"

    def test_custom_tif(self, ibkr_client):
        order = ibkr_client._build_order(
            action="BUY", quantity=1, order_type="MKT", tif="DAY",
        )
        assert order.tif == "DAY"

    def test_parent_id(self, ibkr_client):
        order = ibkr_client._build_order(
            action="BUY", quantity=1, order_type="MKT",
            parent_id=42,
        )
        assert order.parentId == 42

    def test_oca_group(self, ibkr_client):
        order = ibkr_client._build_order(
            action="BUY", quantity=1, order_type="MKT",
            oca_group="my_bracket",
        )
        assert order.ocaGroup == "my_bracket"
        assert order.ocaType == 1

    def test_transmit_default_true(self, ibkr_client):
        order = ibkr_client._build_order(
            action="BUY", quantity=1, order_type="MKT",
        )
        assert order.transmit is True

    def test_transmit_false_for_bracket_parent(self, ibkr_client):
        order = ibkr_client._build_order(
            action="BUY", quantity=1, order_type="MKT",
            transmit=False,
        )
        assert order.transmit is False

    def test_outside_rth(self, ibkr_client):
        order = ibkr_client._build_order(
            action="BUY", quantity=1, order_type="MKT",
            outside_rth=True,
        )
        assert order.outsideRth is True

    def test_account_set(self, ibkr_client):
        order = ibkr_client._build_order(
            action="BUY", quantity=1, order_type="MKT",
            account="DU1234567",
        )
        assert order.account == "DU1234567"

    def test_order_type_case_insensitive(self, ibkr_client):
        order = ibkr_client._build_order(
            action="BUY", quantity=1, order_type="mkt",
        )
        assert order.orderType == "MKT"

    def test_stp_lmt_aliases(self, ibkr_client):
        for alias in ("STP LMT", "STP_LMT", "STPLMT"):
            order = ibkr_client._build_order(
                action="BUY", quantity=1, order_type=alias,
                stop_price=50.0, limit_price=49.5,
            )
            assert order.orderType == "STP LMT"


# ── Serialization Tests ───────────────────────────────────────────────


class TestSerializeTrade:

    def test_basic_serialization(self, ibkr_client, sample_trade):
        result = ibkr_client._serialize_trade(sample_trade)
        assert result["order_id"] == 42
        assert result["action"] == "SELL"
        assert result["quantity"] == 2.0
        assert result["order_type"] == "STP"
        assert result["stop_price"] == 10.895
        assert result["tif"] == "GTC"
        assert result["status"] == "PreSubmitted"
        assert result["filled"] == 0.0
        assert result["remaining"] == 2.0

    def test_contract_info(self, ibkr_client, sample_trade):
        result = ibkr_client._serialize_trade(sample_trade)
        assert result["contract"]["symbol"] == "ZR"
        assert result["contract"]["sec_type"] == "FUT"
        assert result["contract"]["local_symbol"] == "ZRK6"
        assert result["contract"]["con_id"] == 768784616

    def test_sentinel_price_becomes_none(self, ibkr_client, sample_trade):
        """Prices at IB sentinel value (DBL_MAX) should serialize as None."""
        # lmtPrice defaults to DBL_MAX in ib_async
        result = ibkr_client._serialize_trade(sample_trade)
        assert result["limit_price"] is None

    def test_parent_id_included_when_set(self, ibkr_client, sample_trade):
        sample_trade.order.parentId = 99
        result = ibkr_client._serialize_trade(sample_trade)
        assert result["parent_id"] == 99

    def test_parent_id_excluded_when_zero(self, ibkr_client, sample_trade):
        sample_trade.order.parentId = 0
        result = ibkr_client._serialize_trade(sample_trade)
        assert "parent_id" not in result

    def test_oca_group_included_when_set(self, ibkr_client, sample_trade):
        sample_trade.order.ocaGroup = "test_bracket"
        result = ibkr_client._serialize_trade(sample_trade)
        assert result["oca_group"] == "test_bracket"


class TestFindTradeByOrderId:

    def test_finds_matching_trade(self, ibkr_client, sample_trade):
        ibkr_client.ib.openTrades.return_value = [sample_trade]
        found = ibkr_client._find_trade_by_order_id(42)
        assert found is sample_trade

    def test_returns_none_when_not_found(self, ibkr_client, sample_trade):
        ibkr_client.ib.openTrades.return_value = [sample_trade]
        found = ibkr_client._find_trade_by_order_id(999)
        assert found is None

    def test_returns_none_on_empty_list(self, ibkr_client):
        ibkr_client.ib.openTrades.return_value = []
        found = ibkr_client._find_trade_by_order_id(42)
        assert found is None


# ── Place Order Tests (mocked IB) ────────────────────────────────────


class TestPlaceOrder:

    @pytest.mark.asyncio
    async def test_rejects_live_trading_when_disabled(self, ibkr_client):
        """Safety check: live trading must be explicitly enabled."""
        ibkr_client.port = 7496  # live trading port
        with pytest.raises(TradingError, match="Live trading is disabled"):
            await ibkr_client.place_order(
                symbol="ES", sec_type="FUT", exchange="CME",
                action="BUY", quantity=1, order_type="MKT",
                local_symbol="ESM6",
            )

    @pytest.mark.asyncio
    async def test_rejects_oversized_order(self, ibkr_client):
        with pytest.raises(TradingError, match="exceeds max"):
            await ibkr_client.place_order(
                symbol="ES", sec_type="FUT", exchange="CME",
                action="BUY", quantity=9999, order_type="MKT",
                local_symbol="ESM6",
            )

    @pytest.mark.asyncio
    async def test_rejects_invalid_action(self, ibkr_client):
        with pytest.raises(ValidationError, match="Invalid action"):
            await ibkr_client.place_order(
                symbol="ES", sec_type="FUT", exchange="CME",
                action="HOLD", quantity=1, order_type="MKT",
                local_symbol="ESM6",
            )

    @pytest.mark.asyncio
    async def test_rejects_unqualifiable_contract(self, ibkr_client):
        ibkr_client.ib.qualifyContractsAsync = AsyncMock(return_value=[])
        with pytest.raises(ValidationError, match="Could not qualify"):
            await ibkr_client.place_order(
                symbol="FAKE", sec_type="FUT", exchange="CME",
                action="BUY", quantity=1, order_type="MKT",
                local_symbol="FAKE6",
            )

    @pytest.mark.asyncio
    async def test_successful_order_placement(self, ibkr_client, sample_trade):
        # Mock qualify to set conId
        async def mock_qualify(*contracts):
            for c in contracts:
                c.conId = 12345
            return list(contracts)

        ibkr_client.ib.qualifyContractsAsync = mock_qualify
        ibkr_client.ib.placeOrder.return_value = sample_trade

        result = await ibkr_client.place_order(
            symbol="ZR", sec_type="FUT", exchange="CBOT",
            action="SELL", quantity=2, order_type="STP",
            stop_price=10.895, local_symbol="ZRK6",
        )

        assert result["order_id"] == 42
        assert result["action"] == "SELL"
        assert result["status"] == "PreSubmitted"
        ibkr_client.ib.placeOrder.assert_called_once()


# ── Cancel Order Tests ────────────────────────────────────────────────


class TestCancelOrder:

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_order(self, ibkr_client):
        ibkr_client.ib.openTrades.return_value = []
        with pytest.raises(ValidationError, match="No open order found"):
            await ibkr_client.cancel_order(999)

    @pytest.mark.asyncio
    async def test_cancel_existing_order(self, ibkr_client, sample_trade):
        ibkr_client.ib.openTrades.return_value = [sample_trade]
        result = await ibkr_client.cancel_order(42)
        assert result["success"] is True
        assert result["order_id"] == 42
        ibkr_client.ib.cancelOrder.assert_called_once_with(sample_trade.order)


# ── Cancel All Orders Tests ──────────────────────────────────────────


class TestCancelAllOrders:

    @pytest.mark.asyncio
    async def test_cancel_all_empty(self, ibkr_client):
        ibkr_client.ib.openTrades.return_value = []
        result = await ibkr_client.cancel_all_orders()
        assert result["success"] is True
        assert result["cancelled_count"] == 0

    @pytest.mark.asyncio
    async def test_cancel_all_with_orders(self, ibkr_client, sample_trade):
        ibkr_client.ib.openTrades.return_value = [sample_trade]
        result = await ibkr_client.cancel_all_orders()
        assert result["cancelled_count"] == 1
        assert 42 in result["cancelled_order_ids"]


# ── Get Open Orders Tests ────────────────────────────────────────────


class TestGetOpenOrders:

    @pytest.mark.asyncio
    async def test_refreshes_from_ib(self, ibkr_client, sample_trade):
        """Verifies we call reqAllOpenOrdersAsync before reading cache."""
        ibkr_client.ib.reqAllOpenOrdersAsync = AsyncMock()
        ibkr_client.ib.openTrades.return_value = [sample_trade]

        result = await ibkr_client.get_open_orders()
        ibkr_client.ib.reqAllOpenOrdersAsync.assert_called_once()
        assert len(result) == 1
        assert result[0]["order_id"] == 42

    @pytest.mark.asyncio
    async def test_empty_when_no_orders(self, ibkr_client):
        ibkr_client.ib.reqAllOpenOrdersAsync = AsyncMock()
        ibkr_client.ib.openTrades.return_value = []

        result = await ibkr_client.get_open_orders()
        assert result == []
