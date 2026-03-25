"""Tests for MCP tool definitions and schema validation."""

import json
import pytest

from ibkr_mcp_server.tools import server, TOOLS


class TestToolRegistration:

    def test_server_creation(self):
        assert server is not None

    def test_tool_count(self):
        assert len(TOOLS) == 19

    def test_all_tools_have_names(self):
        for tool in TOOLS:
            assert tool.name, f"Tool missing name: {tool}"

    def test_all_tools_have_descriptions(self):
        for tool in TOOLS:
            assert tool.description, f"Tool {tool.name} missing description"

    def test_all_tools_have_schemas(self):
        for tool in TOOLS:
            schema = tool.inputSchema
            assert schema["type"] == "object"
            assert "properties" in schema


class TestToolNames:
    """Verify all expected tools exist."""

    EXPECTED_TOOLS = [
        "get_portfolio", "get_account_summary", "switch_account", "get_accounts",
        "check_shortable_shares", "get_margin_requirements", "short_selling_analysis",
        "get_connection_status", "get_open_orders", "get_completed_orders",
        "place_order", "cancel_order", "cancel_all_orders", "modify_order",
        "get_executions", "get_market_data", "get_historical_bars",
        "what_if_order", "get_contract_details",
    ]

    def test_all_expected_tools_present(self):
        tool_names = {t.name for t in TOOLS}
        for name in self.EXPECTED_TOOLS:
            assert name in tool_names, f"Missing tool: {name}"

    def test_no_unexpected_tools(self):
        tool_names = {t.name for t in TOOLS}
        expected = set(self.EXPECTED_TOOLS)
        unexpected = tool_names - expected
        assert not unexpected, f"Unexpected tools: {unexpected}"


class TestPlaceOrderSchema:
    """Detailed schema tests for the most critical tool."""

    @pytest.fixture
    def schema(self):
        tool = next(t for t in TOOLS if t.name == "place_order")
        return tool.inputSchema

    def test_required_fields(self, schema):
        required = set(schema["required"])
        assert required == {"symbol", "sec_type", "exchange", "action", "quantity", "order_type"}

    def test_order_type_enum(self, schema):
        enum = schema["properties"]["order_type"]["enum"]
        assert "MKT" in enum
        assert "LMT" in enum
        assert "STP" in enum
        assert "STP LMT" in enum
        assert "TRAIL" in enum

    def test_action_enum(self, schema):
        enum = schema["properties"]["action"]["enum"]
        assert enum == ["BUY", "SELL"]

    def test_tif_enum(self, schema):
        enum = schema["properties"]["tif"]["enum"]
        assert "GTC" in enum
        assert "DAY" in enum

    def test_numeric_fields_accept_strings(self, schema):
        """Critical: MCP sends decimals as strings, schema must accept both."""
        numeric_fields = [
            "quantity", "limit_price", "stop_price",
            "trail_stop_price", "trail_amount", "trail_percent",
        ]
        for field in numeric_fields:
            types = schema["properties"][field]["type"]
            assert "string" in types, f"{field} should accept string type"
            assert "number" in types, f"{field} should accept number type"

    def test_has_bracket_order_support(self, schema):
        assert "parent_id" in schema["properties"]
        assert "oca_group" in schema["properties"]

    def test_has_transmit_param(self, schema):
        assert "transmit" in schema["properties"]
        assert schema["properties"]["transmit"]["type"] == "boolean"

    def test_has_trailing_stop_params(self, schema):
        assert "trail_stop_price" in schema["properties"]
        assert "trail_amount" in schema["properties"]
        assert "trail_percent" in schema["properties"]

    def test_no_additional_properties(self, schema):
        assert schema["additionalProperties"] is False


class TestCancelOrderSchema:

    def test_order_id_accepts_string(self):
        """Bug fix verification: cancel_order should accept string order IDs."""
        tool = next(t for t in TOOLS if t.name == "cancel_order")
        types = tool.inputSchema["properties"]["order_id"]["type"]
        assert "string" in types
        assert "integer" in types


class TestModifyOrderSchema:

    def test_order_id_accepts_string(self):
        tool = next(t for t in TOOLS if t.name == "modify_order")
        types = tool.inputSchema["properties"]["order_id"]["type"]
        assert "string" in types

    def test_numeric_fields_accept_strings(self):
        tool = next(t for t in TOOLS if t.name == "modify_order")
        for field in ("quantity", "limit_price", "stop_price"):
            types = tool.inputSchema["properties"][field]["type"]
            assert "string" in types


class TestHistoricalBarsSchema:

    def test_what_to_show_enum(self):
        tool = next(t for t in TOOLS if t.name == "get_historical_bars")
        enum = tool.inputSchema["properties"]["what_to_show"]["enum"]
        assert set(enum) == {"TRADES", "MIDPOINT", "BID", "ASK"}

    def test_required_fields(self):
        tool = next(t for t in TOOLS if t.name == "get_historical_bars")
        assert set(tool.inputSchema["required"]) == {"symbol", "sec_type", "exchange"}
