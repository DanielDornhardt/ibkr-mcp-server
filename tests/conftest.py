"""Pytest configuration and fixtures."""

import pytest
from unittest.mock import MagicMock, AsyncMock, PropertyMock

from ib_async import (
    Contract, Future, Stock, Order, Trade, OrderStatus,
    Fill, Execution, CommissionReport, ContractDetails,
)
from ibkr_mcp_server.client import IBKRClient


@pytest.fixture
def mock_ib():
    """Mock IB object for testing."""
    ib = MagicMock()
    ib.isConnected.return_value = True
    ib.managedAccounts.return_value = ["DU1234567", "DU7654321"]
    return ib


@pytest.fixture
def ibkr_client(mock_ib):
    """IBKR client with mocked IB connection."""
    client = IBKRClient()
    client.ib = mock_ib
    client._connected = True
    client.accounts = ["DU1234567", "DU7654321"]
    client.current_account = "DU1234567"
    return client


@pytest.fixture
def sample_future_contract():
    """A sample qualified futures contract."""
    contract = Future(
        symbol="ZR",
        exchange="CBOT",
        currency="USD",
        localSymbol="ZRK6",
    )
    contract.conId = 768784616
    return contract


@pytest.fixture
def sample_order():
    """A sample stop order."""
    order = Order(
        orderId=42,
        permId=12345,
        action="SELL",
        totalQuantity=2.0,
        orderType="STP",
        auxPrice=10.895,
        tif="GTC",
        account="DU1234567",
    )
    return order


@pytest.fixture
def sample_order_status():
    """A sample order status."""
    status = OrderStatus(
        orderId=42,
        status="PreSubmitted",
        filled=0.0,
        remaining=2.0,
        avgFillPrice=0.0,
    )
    return status


@pytest.fixture
def sample_trade(sample_future_contract, sample_order, sample_order_status):
    """A sample Trade object."""
    trade = Trade(
        contract=sample_future_contract,
        order=sample_order,
        orderStatus=sample_order_status,
    )
    return trade
