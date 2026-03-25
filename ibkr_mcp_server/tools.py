"""MCP tools for IBKR functionality."""

import json
from typing import Any, Sequence

from mcp.server import Server
from mcp.types import Tool, TextContent, CallToolRequest

from .client import ibkr_client
from .utils import validate_symbols, IBKRError


# Create the server instance
server = Server("ibkr-mcp")


# Define all tools
TOOLS = [
    Tool(
        name="get_portfolio",
        description="Retrieve current portfolio positions and P&L from IBKR",
        inputSchema={
            "type": "object",
            "properties": {
                "account": {"type": "string", "description": "Account ID (optional, uses current account if not specified)"}
            },
            "additionalProperties": False
        }
    ),
    Tool(
        name="get_account_summary", 
        description="Get account balances and key metrics from IBKR",
        inputSchema={
            "type": "object",
            "properties": {
                "account": {"type": "string", "description": "Account ID (optional, uses current account if not specified)"}
            },
            "additionalProperties": False
        }
    ),
    Tool(
        name="switch_account",
        description="Switch between IBKR accounts",
        inputSchema={
            "type": "object",
            "properties": {
                "account_id": {"type": "string", "description": "Account ID to switch to"}
            },
            "required": ["account_id"],
            "additionalProperties": False
        }
    ),
    Tool(
        name="get_accounts",
        description="Get available IBKR accounts and current account", 
        inputSchema={"type": "object", "properties": {}, "additionalProperties": False}
    ),
    Tool(
        name="check_shortable_shares",
        description="Check short selling availability for securities",
        inputSchema={
            "type": "object",
            "properties": {
                "symbols": {"type": "string", "description": "Comma-separated list of symbols"},
                "account": {"type": "string", "description": "Account ID (optional, uses current account if not specified)"}
            },
            "required": ["symbols"],
            "additionalProperties": False
        }
    ),
    Tool(
        name="get_margin_requirements",
        description="Get margin requirements for securities",
        inputSchema={
            "type": "object",
            "properties": {
                "symbols": {"type": "string", "description": "Comma-separated list of symbols"},
                "account": {"type": "string", "description": "Account ID (optional, uses current account if not specified)"}
            },
            "required": ["symbols"],
            "additionalProperties": False
        }
    ),
    Tool(
        name="short_selling_analysis",
        description="Complete short selling analysis: availability, margin requirements, and summary",
        inputSchema={
            "type": "object",
            "properties": {
                "symbols": {"type": "string", "description": "Comma-separated list of symbols"},
                "account": {"type": "string", "description": "Account ID (optional, uses current account if not specified)"}
            },
            "required": ["symbols"],
            "additionalProperties": False
        }
    ),
    Tool(
        name="get_connection_status",
        description="Check IBKR TWS/Gateway connection status and account information",
        inputSchema={"type": "object", "properties": {}, "additionalProperties": False}
    ),
    Tool(
        name="get_open_orders",
        description="Get all open/pending orders from IBKR",
        inputSchema={"type": "object", "properties": {}, "additionalProperties": False}
    ),
    Tool(
        name="get_completed_orders",
        description="Get recently completed orders (filled, cancelled) from IBKR",
        inputSchema={"type": "object", "properties": {}, "additionalProperties": False}
    ),
    Tool(
        name="place_order",
        description="Place a new order (futures or stock). Supports Market, Limit, Stop, Stop Limit, and Trailing Stop orders. Defaults to GTC. Use parent_id to create bracket orders (child activates only when parent fills).",
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Root symbol (e.g. ES, NQ, GC, CL, ZB)"},
                "sec_type": {"type": "string", "enum": ["FUT", "STK"], "description": "Security type: FUT for futures, STK for stocks"},
                "exchange": {"type": "string", "description": "Exchange (e.g. CME, COMEX, NYMEX, CBOT, SMART)"},
                "action": {"type": "string", "enum": ["BUY", "SELL"], "description": "Order action"},
                "quantity": {"type": ["number", "string"], "description": "Number of contracts or shares"},
                "order_type": {"type": "string", "enum": ["MKT", "LMT", "STP", "STP LMT", "TRAIL"], "description": "Order type"},
                "limit_price": {"type": ["number", "string"], "description": "Limit price (required for LMT and STP LMT)"},
                "stop_price": {"type": ["number", "string"], "description": "Stop price (required for STP and STP LMT)"},
                "trail_stop_price": {"type": ["number", "string"], "description": "Initial stop price for TRAIL orders"},
                "trail_amount": {"type": ["number", "string"], "description": "Fixed trailing amount in points/dollars for TRAIL orders"},
                "trail_percent": {"type": ["number", "string"], "description": "Trailing percentage for TRAIL orders (alternative to trail_amount)"},
                "tif": {"type": "string", "enum": ["GTC", "DAY", "IOC", "GTD"], "default": "GTC", "description": "Time in force (default: GTC)"},
                "currency": {"type": "string", "default": "USD", "description": "Currency (default: USD)"},
                "last_trade_date": {"type": "string", "description": "Futures expiry YYYYMMDD or YYYYMM (e.g. 20260620)"},
                "local_symbol": {"type": "string", "description": "Local symbol override (e.g. ESM6, NQM6)"},
                "outside_rth": {"type": "boolean", "default": False, "description": "Allow execution outside regular trading hours"},
                "parent_id": {"type": ["integer", "string"], "description": "Parent order ID for bracket orders. Child order only activates when parent fills."},
                "oca_group": {"type": "string", "description": "OCA (One Cancels All) group name. Orders in the same group cancel each other when one fills."},
                "transmit": {"type": "boolean", "default": True, "description": "Transmit order to exchange. Set False on bracket parent, True on last child for atomic bracket placement."},
                "account": {"type": "string", "description": "Account ID (optional, uses current)"}
            },
            "required": ["symbol", "sec_type", "exchange", "action", "quantity", "order_type"],
            "additionalProperties": False
        }
    ),
    Tool(
        name="cancel_order",
        description="Cancel a specific open order by order ID",
        inputSchema={
            "type": "object",
            "properties": {
                "order_id": {"type": ["integer", "string"], "description": "The order ID to cancel"}
            },
            "required": ["order_id"],
            "additionalProperties": False
        }
    ),
    Tool(
        name="cancel_all_orders",
        description="Cancel ALL open orders. Use with caution!",
        inputSchema={"type": "object", "properties": {}, "additionalProperties": False}
    ),
    Tool(
        name="modify_order",
        description="Modify an existing open order (change price, quantity, or time-in-force)",
        inputSchema={
            "type": "object",
            "properties": {
                "order_id": {"type": ["integer", "string"], "description": "The order ID to modify"},
                "quantity": {"type": ["number", "string"], "description": "New quantity (optional)"},
                "limit_price": {"type": ["number", "string"], "description": "New limit price (optional)"},
                "stop_price": {"type": ["number", "string"], "description": "New stop price (optional)"},
                "tif": {"type": "string", "enum": ["GTC", "DAY", "IOC", "GTD"], "description": "New time in force (optional)"}
            },
            "required": ["order_id"],
            "additionalProperties": False
        }
    ),
    Tool(
        name="get_executions",
        description="Get execution reports (fills) with optional time and symbol filters. IB stores several days of history.",
        inputSchema={
            "type": "object",
            "properties": {
                "since": {"type": "string", "description": "Filter from this time (yyyymmdd-hh:mm:ss or yyyymmdd). Optional."},
                "symbol": {"type": "string", "description": "Filter by symbol. Optional."}
            },
            "additionalProperties": False
        }
    ),
    Tool(
        name="get_market_data",
        description="Get snapshot market data (bid/ask/last/high/low/open/close/volume) for a futures or stock contract.",
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Root symbol (e.g. ES, NQ, GC)"},
                "sec_type": {"type": "string", "enum": ["FUT", "STK"], "description": "Security type"},
                "exchange": {"type": "string", "description": "Exchange (e.g. CME, CBOT, NYBOT)"},
                "currency": {"type": "string", "default": "USD", "description": "Currency (default: USD)"},
                "last_trade_date": {"type": "string", "description": "Futures expiry YYYYMMDD or YYYYMM"},
                "local_symbol": {"type": "string", "description": "Local symbol override (e.g. ESM6)"}
            },
            "required": ["symbol", "sec_type", "exchange"],
            "additionalProperties": False
        }
    ),
    Tool(
        name="get_historical_bars",
        description="Get historical OHLCV bars for charting and analysis (ATR, trends, patterns). Supports 1min to monthly bars.",
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Root symbol (e.g. ES, NQ, GC)"},
                "sec_type": {"type": "string", "enum": ["FUT", "STK"], "description": "Security type"},
                "exchange": {"type": "string", "description": "Exchange (e.g. CME, CBOT, NYBOT)"},
                "duration": {"type": "string", "default": "1 M", "description": "Time span: '1 D', '1 W', '1 M', '3 M', '1 Y'"},
                "bar_size": {"type": "string", "default": "1 day", "description": "Bar size: '1 min', '5 mins', '15 mins', '1 hour', '1 day', '1 week'"},
                "what_to_show": {"type": "string", "default": "TRADES", "enum": ["TRADES", "MIDPOINT", "BID", "ASK"], "description": "Data type"},
                "use_rth": {"type": "boolean", "default": False, "description": "Regular trading hours only"},
                "currency": {"type": "string", "default": "USD", "description": "Currency"},
                "last_trade_date": {"type": "string", "description": "Futures expiry YYYYMMDD or YYYYMM"},
                "local_symbol": {"type": "string", "description": "Local symbol override"}
            },
            "required": ["symbol", "sec_type", "exchange"],
            "additionalProperties": False
        }
    ),
    Tool(
        name="what_if_order",
        description="Preview margin impact of a hypothetical order without actually placing it. Shows margin before/after, commission, and warnings.",
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Root symbol"},
                "sec_type": {"type": "string", "enum": ["FUT", "STK"], "description": "Security type"},
                "exchange": {"type": "string", "description": "Exchange"},
                "action": {"type": "string", "enum": ["BUY", "SELL"], "description": "Order action"},
                "quantity": {"type": ["number", "string"], "description": "Number of contracts or shares"},
                "order_type": {"type": "string", "enum": ["MKT", "LMT", "STP", "STP LMT"], "description": "Order type"},
                "limit_price": {"type": ["number", "string"], "description": "Limit price (for LMT orders)"},
                "stop_price": {"type": ["number", "string"], "description": "Stop price (for STP orders)"},
                "currency": {"type": "string", "default": "USD", "description": "Currency"},
                "last_trade_date": {"type": "string", "description": "Futures expiry"},
                "local_symbol": {"type": "string", "description": "Local symbol override"}
            },
            "required": ["symbol", "sec_type", "exchange", "action", "quantity", "order_type"],
            "additionalProperties": False
        }
    ),
    Tool(
        name="get_contract_details",
        description="Look up contract details: tick size, multiplier, trading hours, valid exchanges, and more.",
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Root symbol (e.g. ES, ZR, KC)"},
                "sec_type": {"type": "string", "enum": ["FUT", "STK"], "description": "Security type"},
                "exchange": {"type": "string", "default": "", "description": "Exchange (optional — omit to search all)"},
                "currency": {"type": "string", "default": "USD", "description": "Currency"},
                "last_trade_date": {"type": "string", "description": "Futures expiry YYYYMMDD or YYYYMM"},
                "local_symbol": {"type": "string", "description": "Local symbol override"}
            },
            "required": ["symbol", "sec_type"],
            "additionalProperties": False
        }
    )
]


# Register tools list handler
@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools."""
    return TOOLS


# Register tool call handler  
@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> Sequence[TextContent]:
    """Handle tool calls."""
    try:
        if name == "get_portfolio":
            account = arguments.get("account")
            positions = await ibkr_client.get_portfolio(account)
            return [TextContent(
                type="text",
                text=json.dumps(positions, indent=2)
            )]
            
        elif name == "get_account_summary":
            account = arguments.get("account")
            summary = await ibkr_client.get_account_summary(account)
            return [TextContent(
                type="text", 
                text=json.dumps(summary, indent=2)
            )]
            
        elif name == "switch_account":
            account_id = arguments["account_id"]
            result = await ibkr_client.switch_account(account_id)
            return [TextContent(
                type="text",
                text=json.dumps(result, indent=2)
            )]
            
        elif name == "get_accounts":
            accounts = await ibkr_client.get_accounts()
            return [TextContent(
                type="text",
                text=json.dumps(accounts, indent=2)
            )]
            
        elif name == "check_shortable_shares":
            symbols = arguments["symbols"]
            account = arguments.get("account")
            try:
                symbol_list = validate_symbols(symbols)
                results = []
                for symbol in symbol_list:
                    shortable_info = await ibkr_client.get_shortable_shares(symbol, account)
                    results.append({
                        "symbol": symbol,
                        "shortable_shares": shortable_info
                    })
                return [TextContent(
                    type="text",
                    text=json.dumps(results, indent=2)
                )]
            except Exception as e:
                return [TextContent(
                    type="text",
                    text=f"Error checking shortable shares: {str(e)}"
                )]
                
        elif name == "get_margin_requirements":
            symbols = arguments["symbols"]
            account = arguments.get("account")
            try:
                symbol_list = validate_symbols(symbols)
                results = []
                for symbol in symbol_list:
                    margin_info = await ibkr_client.get_margin_requirements(symbol, account)
                    results.append({
                        "symbol": symbol,
                        "margin_requirements": margin_info
                    })
                return [TextContent(
                    type="text",
                    text=json.dumps(results, indent=2)
                )]
            except Exception as e:
                return [TextContent(
                    type="text",
                    text=f"Error getting margin requirements: {str(e)}"
                )]
                
        elif name == "short_selling_analysis":
            symbols = arguments["symbols"]
            account = arguments.get("account")
            try:
                symbol_list = validate_symbols(symbols)
                analysis = await ibkr_client.short_selling_analysis(symbol_list, account)
                return [TextContent(
                    type="text",
                    text=json.dumps(analysis, indent=2)
                )]
            except Exception as e:
                return [TextContent(
                    type="text",
                    text=f"Error performing short selling analysis: {str(e)}"
                )]
                
        elif name == "get_connection_status":
            status = {
                "connected": ibkr_client.is_connected(),
                "host": ibkr_client.host,
                "port": ibkr_client.port,
                "client_id": ibkr_client.client_id,
                "current_account": ibkr_client.current_account,
                "available_accounts": ibkr_client.accounts,
                "paper_trading": ibkr_client.is_paper
            }
            return [TextContent(
                type="text",
                text=json.dumps(status, indent=2)
            )]

        elif name == "get_open_orders":
            orders = await ibkr_client.get_open_orders()
            return [TextContent(
                type="text",
                text=json.dumps(orders, indent=2)
            )]

        elif name == "get_completed_orders":
            orders = await ibkr_client.get_completed_orders()
            return [TextContent(
                type="text",
                text=json.dumps(orders, indent=2)
            )]

        elif name == "place_order":
            result = await ibkr_client.place_order(
                symbol=arguments["symbol"],
                sec_type=arguments["sec_type"],
                exchange=arguments["exchange"],
                action=arguments["action"],
                quantity=float(arguments["quantity"]),
                order_type=arguments["order_type"],
                limit_price=float(arguments["limit_price"]) if arguments.get("limit_price") is not None else None,
                stop_price=float(arguments["stop_price"]) if arguments.get("stop_price") is not None else None,
                trail_stop_price=float(arguments["trail_stop_price"]) if arguments.get("trail_stop_price") is not None else None,
                trail_amount=float(arguments["trail_amount"]) if arguments.get("trail_amount") is not None else None,
                trail_percent=float(arguments["trail_percent"]) if arguments.get("trail_percent") is not None else None,
                tif=arguments.get("tif", "GTC"),
                currency=arguments.get("currency", "USD"),
                last_trade_date=arguments.get("last_trade_date"),
                local_symbol=arguments.get("local_symbol"),
                outside_rth=arguments.get("outside_rth", False),
                parent_id=int(arguments["parent_id"]) if arguments.get("parent_id") is not None else None,
                oca_group=arguments.get("oca_group"),
                transmit=arguments.get("transmit", True),
                account=arguments.get("account"),
            )
            return [TextContent(
                type="text",
                text=json.dumps(result, indent=2)
            )]

        elif name == "cancel_order":
            result = await ibkr_client.cancel_order(int(arguments["order_id"]))
            return [TextContent(
                type="text",
                text=json.dumps(result, indent=2)
            )]

        elif name == "cancel_all_orders":
            result = await ibkr_client.cancel_all_orders()
            return [TextContent(
                type="text",
                text=json.dumps(result, indent=2)
            )]

        elif name == "modify_order":
            result = await ibkr_client.modify_order(
                order_id=int(arguments["order_id"]),
                quantity=float(arguments["quantity"]) if arguments.get("quantity") is not None else None,
                limit_price=float(arguments["limit_price"]) if arguments.get("limit_price") is not None else None,
                stop_price=float(arguments["stop_price"]) if arguments.get("stop_price") is not None else None,
                tif=arguments.get("tif"),
            )
            return [TextContent(
                type="text",
                text=json.dumps(result, indent=2)
            )]

        elif name == "get_executions":
            result = await ibkr_client.get_executions(
                since=arguments.get("since"),
                symbol=arguments.get("symbol"),
            )
            return [TextContent(
                type="text",
                text=json.dumps(result, indent=2)
            )]

        elif name == "get_market_data":
            result = await ibkr_client.get_market_data(
                symbol=arguments["symbol"],
                sec_type=arguments["sec_type"],
                exchange=arguments["exchange"],
                currency=arguments.get("currency", "USD"),
                last_trade_date=arguments.get("last_trade_date"),
                local_symbol=arguments.get("local_symbol"),
            )
            return [TextContent(
                type="text",
                text=json.dumps(result, indent=2)
            )]

        elif name == "get_historical_bars":
            result = await ibkr_client.get_historical_bars(
                symbol=arguments["symbol"],
                sec_type=arguments["sec_type"],
                exchange=arguments["exchange"],
                duration=arguments.get("duration", "1 M"),
                bar_size=arguments.get("bar_size", "1 day"),
                what_to_show=arguments.get("what_to_show", "TRADES"),
                use_rth=arguments.get("use_rth", False),
                currency=arguments.get("currency", "USD"),
                last_trade_date=arguments.get("last_trade_date"),
                local_symbol=arguments.get("local_symbol"),
            )
            return [TextContent(
                type="text",
                text=json.dumps(result, indent=2)
            )]

        elif name == "what_if_order":
            result = await ibkr_client.what_if_order(
                symbol=arguments["symbol"],
                sec_type=arguments["sec_type"],
                exchange=arguments["exchange"],
                action=arguments["action"],
                quantity=float(arguments["quantity"]),
                order_type=arguments["order_type"],
                limit_price=float(arguments["limit_price"]) if arguments.get("limit_price") is not None else None,
                stop_price=float(arguments["stop_price"]) if arguments.get("stop_price") is not None else None,
                currency=arguments.get("currency", "USD"),
                last_trade_date=arguments.get("last_trade_date"),
                local_symbol=arguments.get("local_symbol"),
            )
            return [TextContent(
                type="text",
                text=json.dumps(result, indent=2)
            )]

        elif name == "get_contract_details":
            result = await ibkr_client.get_contract_details(
                symbol=arguments["symbol"],
                sec_type=arguments["sec_type"],
                exchange=arguments.get("exchange", ""),
                currency=arguments.get("currency", "USD"),
                last_trade_date=arguments.get("last_trade_date"),
                local_symbol=arguments.get("local_symbol"),
            )
            return [TextContent(
                type="text",
                text=json.dumps(result, indent=2)
            )]

        else:
            return [TextContent(
                type="text",
                text=f"Unknown tool: {name}"
            )]

    except Exception as e:
        return [TextContent(
            type="text",
            text=f"Error executing tool {name}: {str(e)}"
        )]
