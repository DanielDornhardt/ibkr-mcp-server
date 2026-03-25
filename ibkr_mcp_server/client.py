"""IBKR Client with advanced trading capabilities."""

import asyncio
import logging
from typing import Dict, List, Optional, Union
from decimal import Decimal

from ib_async import (
    IB, Stock, Future, Order, LimitOrder, StopOrder, MarketOrder, Trade,
    ExecutionFilter, util
)
from .config import settings
from .utils import rate_limit, retry_on_failure, safe_float, safe_int, ValidationError, ConnectionError as IBKRConnectionError, TradingError


class IBKRClient:
    """Enhanced IBKR client with multi-account and short selling support."""
    
    def __init__(self):
        self.ib: Optional[IB] = None
        self.logger = logging.getLogger(__name__)
        
        # Connection settings
        self.host = settings.ibkr_host
        self.port = settings.ibkr_port
        self.client_id = settings.ibkr_client_id
        self.max_reconnect_attempts = settings.max_reconnect_attempts
        self.reconnect_delay = settings.reconnect_delay
        self.reconnect_attempts = 0
        
        # Account management
        self.accounts: List[str] = []
        self.current_account: Optional[str] = settings.ibkr_default_account
        
        # Connection state
        self._connected = False
        self._connecting = False
    
    @property
    def is_paper(self) -> bool:
        """Check if this is a paper trading connection."""
        return self.port in [7497, 4002]  # Common paper trading ports
    
    async def _ensure_connected(self) -> bool:
        """Ensure IBKR connection is active, reconnect if needed."""
        if self.is_connected():
            return True
        
        try:
            await self.connect()
            return self.is_connected()
        except Exception as e:
            self.logger.error(f"Failed to ensure connection: {e}")
            return False
    
    @retry_on_failure(max_attempts=3)
    async def connect(self) -> bool:
        """Establish connection and discover accounts."""
        if self._connected and self.ib and self.ib.isConnected():
            return True
        
        if self._connecting:
            # Wait for ongoing connection attempt
            while self._connecting:
                await asyncio.sleep(0.1)
            return self._connected
        
        self._connecting = True
        
        try:
            self.ib = IB()
            
            self.logger.info(f"Connecting to IBKR at {self.host}:{self.port}...")
            await self.ib.connectAsync(
                host=self.host,
                port=self.port,
                clientId=self.client_id,
                timeout=10
            )
            
            # Setup event handlers
            self.ib.disconnectedEvent += self._on_disconnect
            self.ib.errorEvent += self._on_error
            
            # Wait for connection to stabilize
            await asyncio.sleep(2)
            
            # Discover accounts
            self.accounts = self.ib.managedAccounts()
            if self.accounts:
                if not self.current_account or self.current_account not in self.accounts:
                    self.current_account = self.accounts[0]
                
                self.logger.info(f"Connected to IBKR. Accounts: {self.accounts}")
                self.logger.info(f"Current account: {self.current_account}")
            else:
                self.logger.warning("No managed accounts found")
            
            self._connected = True
            self.reconnect_attempts = 0
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to connect to IBKR: {e}")
            raise IBKRConnectionError(f"Connection failed: {e}")
        finally:
            self._connecting = False
    
    async def disconnect(self):
        """Clean disconnection."""
        if self.ib and self.ib.isConnected():
            self.ib.disconnect()
            self._connected = False
            self.logger.info("IBKR disconnected")
    
    def _on_disconnect(self):
        """Handle disconnection with automatic reconnection."""
        self._connected = False
        self.logger.warning("IBKR disconnected, scheduling reconnection...")
        asyncio.create_task(self._reconnect())
    
    def _on_error(self, reqId, errorCode, errorString, contract):
        """Centralized error logging."""
        # Don't log certain routine messages as errors
        if errorCode in [2104, 2106, 2158]:  # Market data warnings
            self.logger.debug(f"IBKR Info {errorCode}: {errorString}")
        else:
            self.logger.error(f"IBKR Error {errorCode}: {errorString} (reqId: {reqId})")
    
    async def _reconnect(self):
        """Background reconnection task."""
        try:
            await asyncio.sleep(self.reconnect_delay)
            await self.connect()
        except Exception as e:
            self.logger.error(f"Reconnection failed: {e}")
    
    def is_connected(self) -> bool:
        """Check connection status."""
        return self._connected and self.ib is not None and self.ib.isConnected()
    
    @rate_limit(calls_per_second=1.0)
    async def get_portfolio(self, account: Optional[str] = None) -> List[Dict]:
        """Get portfolio positions."""
        try:
            if not await self._ensure_connected():
                raise ConnectionError("Not connected to IBKR")
            
            account = account or self.current_account
            
            positions = await self.ib.reqPositionsAsync()
            
            portfolio = []
            for pos in positions:
                if not account or pos.account == account:
                    portfolio.append(self._serialize_position(pos))
            
            return portfolio
            
        except Exception as e:
            self.logger.error(f"Portfolio request failed: {e}")
            raise RuntimeError(f"IBKR API error: {str(e)}")
    
    @rate_limit(calls_per_second=1.0)
    async def get_account_summary(self, account: Optional[str] = None) -> List[Dict]:
        """Get account summary."""
        try:
            if not await self._ensure_connected():
                raise ConnectionError("Not connected to IBKR")
            
            account = account or self.current_account or "All"
            
            summary_tags = [
                'TotalCashValue', 'NetLiquidation', 'UnrealizedPnL', 'RealizedPnL',
                'GrossPositionValue', 'BuyingPower', 'EquityWithLoanValue',
                'PreviousDayEquityWithLoanValue', 'FullInitMarginReq', 'FullMaintMarginReq'
            ]
            
            account_values = await self.ib.reqAccountSummaryAsync(account, ','.join(summary_tags))
            
            return [self._serialize_account_value(av) for av in account_values]
            
        except Exception as e:
            self.logger.error(f"Account summary request failed: {e}")
            raise RuntimeError(f"IBKR API error: {str(e)}")
    
    @rate_limit(calls_per_second=0.5)
    async def get_shortable_shares(self, symbol: str, account: str = None) -> Dict:
        """Get short selling information for a symbol."""
        try:
            if not await self._ensure_connected():
                raise ConnectionError("Not connected to IBKR")
            
            contract = Stock(symbol, 'SMART', 'USD')
            
            # Qualify the contract
            qualified_contracts = await self.ib.reqContractDetailsAsync(contract)
            if not qualified_contracts:
                return {"error": "Contract not found"}
            
            qualified_contract = qualified_contracts[0].contract
            
            # Request shortable shares
            shortable_shares = await self.ib.reqShortableSharesAsync(qualified_contract)
            
            # Get current market data
            ticker = self.ib.reqMktData(qualified_contract, '', False, False)
            await asyncio.sleep(1.5)  # Wait for market data
            
            result = {
                "symbol": symbol,
                "shortable_shares": shortable_shares if shortable_shares != -1 else "Unlimited",
                "current_price": safe_float(ticker.last or ticker.close),
                "bid": safe_float(ticker.bid),
                "ask": safe_float(ticker.ask),
                "contract_id": qualified_contract.conId
            }
            
            # Clean up ticker
            self.ib.cancelMktData(qualified_contract)
            
            return result
            
        except Exception as e:
            self.logger.error(f"Error getting shortable shares for {symbol}: {e}")
            return {"error": str(e)}

    @retry_on_failure(max_attempts=2)
    async def get_margin_requirements(self, symbol: str, account: str = None) -> Dict:
        """Get margin requirements for a symbol."""
        try:
            if not await self._ensure_connected():
                raise ConnectionError("Not connected to IBKR")
                
            # Create contract
            contract = Stock(symbol, 'SMART', 'USD')
            await self.ib.qualifyContractsAsync([contract])
            
            if not contract.conId:
                return {"error": f"Invalid symbol: {symbol}"}
            
            # Get margin requirements - simplified for now
            # Note: IBKR API doesn't provide direct margin requirements
            # This would typically require additional market data subscriptions
            margin_info = {
                "symbol": symbol,
                "contract_id": contract.conId,
                "exchange": contract.exchange,
                "margin_requirement": "Market data subscription required",
                "note": "Use TWS for detailed margin calculations"
            }
            
            return margin_info
            
        except Exception as e:
            self.logger.error(f"Error getting margin info for {symbol}: {e}")
            return {"error": str(e)}

    async def short_selling_analysis(self, symbols: List[str], account: str = None) -> Dict:
        """Complete short selling analysis for multiple symbols."""
        try:
            if not await self._ensure_connected():
                raise ConnectionError("Not connected to IBKR")
            
            analysis = {
                "account": account or self.current_account,
                "symbols_analyzed": symbols,
                "shortable_data": {},
                "margin_data": {},
                "summary": {
                    "total_symbols": len(symbols),
                    "shortable_count": 0,
                    "errors": []
                }
            }
            
            # Get shortable shares data
            for symbol in symbols:
                try:
                    shortable_info = await self.get_shortable_shares(symbol, account)
                    analysis["shortable_data"][symbol] = shortable_info
                    
                    if "error" not in shortable_info:
                        analysis["summary"]["shortable_count"] += 1
                except Exception as e:
                    analysis["summary"]["errors"].append(f"{symbol}: {str(e)}")
            
            # Get margin requirements
            for symbol in symbols:
                try:
                    margin_info = await self.get_margin_requirements(symbol, account)
                    analysis["margin_data"][symbol] = margin_info
                except Exception as e:
                    analysis["summary"]["errors"].append(f"{symbol} margin: {str(e)}")
            
            return analysis
            
        except Exception as e:
            self.logger.error(f"Error in short selling analysis: {e}")
            return {"error": str(e)}
    
    async def switch_account(self, account_id: str) -> Dict:
        """Switch to a different IBKR account."""
        try:
            if account_id not in self.accounts:
                self.logger.error(f"Account {account_id} not found. Available: {self.accounts}")
                return {
                    "success": False,
                    "message": f"Account {account_id} not found",
                    "current_account": self.current_account,
                    "available_accounts": self.accounts
                }
            
            self.current_account = account_id
            self.logger.info(f"Switched to account: {account_id}")
            
            return {
                "success": True,
                "message": f"Switched to account: {account_id}",
                "current_account": self.current_account,
                "available_accounts": self.accounts
            }
            
        except Exception as e:
            self.logger.error(f"Error switching account: {e}")
            return {"success": False, "error": str(e)}

    async def get_accounts(self) -> Dict[str, Union[str, List[str]]]:
        """Get available accounts information."""
        try:
            if not await self._ensure_connected():
                await self.connect()
            
            return {
                "current_account": self.current_account,
                "available_accounts": self.accounts,
                "connected": self.is_connected(),
                "paper_trading": self.is_paper
            }
            
        except Exception as e:
            self.logger.error(f"Error getting accounts: {e}")
            return {"error": str(e)}
    
    # ── Order Management ──────────────────────────────────────────────

    async def get_open_orders(self) -> List[Dict]:
        """Get all open orders (refreshed from IB, not just local cache)."""
        try:
            if not await self._ensure_connected():
                raise ConnectionError("Not connected to IBKR")

            # Refresh from IB so we see orders placed by other clients (TWS, mobile)
            await self.ib.reqAllOpenOrdersAsync()
            trades = self.ib.openTrades()
            return [self._serialize_trade(t) for t in trades]

        except Exception as e:
            self.logger.error(f"Failed to get open orders: {e}")
            raise RuntimeError(f"IBKR API error: {str(e)}")

    async def place_order(
        self,
        symbol: str,
        sec_type: str,
        exchange: str,
        action: str,
        quantity: float,
        order_type: str,
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
        trail_stop_price: Optional[float] = None,
        trail_amount: Optional[float] = None,
        trail_percent: Optional[float] = None,
        tif: str = "GTC",
        currency: str = "USD",
        last_trade_date: Optional[str] = None,
        local_symbol: Optional[str] = None,
        outside_rth: bool = False,
        parent_id: Optional[int] = None,
        oca_group: Optional[str] = None,
        transmit: bool = True,
        account: Optional[str] = None,
    ) -> Dict:
        """Place an order for a futures contract (or stock).

        Args:
            symbol: Root symbol (e.g. "ES", "NQ", "GC").
            sec_type: Security type — "FUT" for futures, "STK" for stock.
            exchange: Exchange (e.g. "CME", "COMEX", "NYMEX", "SMART").
            action: "BUY" or "SELL".
            quantity: Number of contracts / shares.
            order_type: "MKT", "LMT", "STP", "STP LMT", "TRAIL".
            limit_price: Required for LMT and STP LMT orders.
            stop_price: Required for STP and STP LMT orders.
            trail_stop_price: Initial stop price for TRAIL orders.
            trail_amount: Fixed trailing amount in points for TRAIL orders.
            trail_percent: Trailing percentage for TRAIL orders.
            tif: Time in force — "GTC", "DAY", "IOC", "GTD". Default GTC.
            currency: Contract currency. Default USD.
            last_trade_date: Expiry for futures (YYYYMMDD or YYYYMM).
            local_symbol: Local symbol override (e.g. "ESM6").
            outside_rth: Allow execution outside regular trading hours.
            parent_id: Parent order ID for bracket orders.
            oca_group: OCA group name — orders in same group cancel each other.
            transmit: Transmit to exchange. False on bracket parent, True on last child.
            account: Account ID (uses current account if omitted).
        """
        try:
            if not await self._ensure_connected():
                raise ConnectionError("Not connected to IBKR")

            # Safety checks
            if not settings.enable_live_trading and not self.is_paper:
                raise TradingError(
                    "Live trading is disabled. Set ENABLE_LIVE_TRADING=true or use paper trading."
                )

            if quantity > settings.max_order_size:
                raise TradingError(
                    f"Order size {quantity} exceeds max allowed {settings.max_order_size}"
                )

            if action not in ("BUY", "SELL"):
                raise ValidationError(f"Invalid action: {action}. Must be BUY or SELL.")

            # Build contract
            contract = self._build_contract(
                symbol=symbol,
                sec_type=sec_type,
                exchange=exchange,
                currency=currency,
                last_trade_date=last_trade_date,
                local_symbol=local_symbol,
            )

            # Qualify contract with IBKR
            qualified = await self.ib.qualifyContractsAsync(contract)
            if not qualified or not contract.conId:
                raise ValidationError(
                    f"Could not qualify contract: {symbol} {sec_type} {exchange}"
                )

            # Build order
            order = self._build_order(
                action=action,
                quantity=quantity,
                order_type=order_type,
                limit_price=limit_price,
                stop_price=stop_price,
                trail_stop_price=trail_stop_price,
                trail_amount=trail_amount,
                trail_percent=trail_percent,
                tif=tif,
                outside_rth=outside_rth,
                parent_id=parent_id,
                oca_group=oca_group,
                transmit=transmit,
                account=account or self.current_account,
            )

            # Place the order
            trade: Trade = self.ib.placeOrder(contract, order)

            # Give IB a moment to acknowledge
            await asyncio.sleep(0.5)

            return self._serialize_trade(trade)

        except (TradingError, ValidationError):
            raise
        except Exception as e:
            self.logger.error(f"Failed to place order: {e}")
            raise RuntimeError(f"IBKR API error: {str(e)}")

    async def cancel_order(self, order_id: int) -> Dict:
        """Cancel a specific order by order ID."""
        try:
            if not await self._ensure_connected():
                raise ConnectionError("Not connected to IBKR")

            # Find the trade with this order ID
            trade = self._find_trade_by_order_id(order_id)
            if not trade:
                raise ValidationError(f"No open order found with ID {order_id}")

            self.ib.cancelOrder(trade.order)
            await asyncio.sleep(0.5)

            return {
                "success": True,
                "order_id": order_id,
                "message": f"Cancel requested for order {order_id}",
                "status": trade.orderStatus.status,
            }

        except ValidationError:
            raise
        except Exception as e:
            self.logger.error(f"Failed to cancel order {order_id}: {e}")
            raise RuntimeError(f"IBKR API error: {str(e)}")

    async def get_completed_orders(self) -> List[Dict]:
        """Get recently completed (filled, cancelled) orders."""
        try:
            if not await self._ensure_connected():
                raise ConnectionError("Not connected to IBKR")

            fills = await self.ib.reqCompletedOrdersAsync(apiOnly=False)
            results = []
            for fill in fills:
                contract = fill.contract
                order = fill.order
                completion = fill.orderStatus
                results.append({
                    "order_id": order.orderId,
                    "perm_id": order.permId,
                    "action": order.action,
                    "quantity": safe_float(order.totalQuantity),
                    "order_type": order.orderType,
                    "limit_price": safe_float(order.lmtPrice) if order.lmtPrice < 1e300 else None,
                    "stop_price": safe_float(order.auxPrice) if order.auxPrice < 1e300 else None,
                    "tif": order.tif,
                    "status": completion.status,
                    "filled": safe_float(completion.filled),
                    "avg_fill_price": safe_float(completion.avgFillPrice),
                    "contract": {
                        "symbol": contract.symbol,
                        "sec_type": contract.secType,
                        "exchange": contract.exchange,
                        "currency": contract.currency,
                        "local_symbol": contract.localSymbol,
                        "con_id": contract.conId,
                    },
                    "account": order.account,
                })
            return results

        except Exception as e:
            self.logger.error(f"Failed to get completed orders: {e}")
            raise RuntimeError(f"IBKR API error: {str(e)}")

    # ── Market Data & Research ─────────────────────────────────────────

    async def get_executions(self, since: Optional[str] = None, symbol: Optional[str] = None) -> List[Dict]:
        """Get execution reports (fills). IB stores several days of history.

        Args:
            since: Filter from this time (format: yyyymmdd-hh:mm:ss or yyyymmdd). Optional.
            symbol: Filter by symbol. Optional.
        """
        try:
            if not await self._ensure_connected():
                raise ConnectionError("Not connected to IBKR")

            exec_filter = ExecutionFilter()
            if since:
                exec_filter.time = since
            if symbol:
                exec_filter.symbol = symbol.upper()

            fills = await self.ib.reqExecutionsAsync(exec_filter)
            results = []
            for fill in fills:
                execution = fill.execution
                results.append({
                    "exec_id": execution.execId,
                    "time": execution.time,
                    "action": execution.side,
                    "quantity": safe_float(execution.shares),
                    "price": safe_float(execution.price),
                    "avg_price": safe_float(execution.avgPrice),
                    "cum_qty": safe_float(execution.cumQty),
                    "order_id": execution.orderId,
                    "perm_id": execution.permId,
                    "contract": {
                        "symbol": fill.contract.symbol,
                        "sec_type": fill.contract.secType,
                        "exchange": fill.contract.exchange,
                        "local_symbol": fill.contract.localSymbol,
                        "con_id": fill.contract.conId,
                    },
                    "commission": safe_float(fill.commissionReport.commission) if fill.commissionReport else None,
                    "realized_pnl": safe_float(fill.commissionReport.realizedPNL) if fill.commissionReport else None,
                    "account": execution.acctNumber,
                })
            return results

        except Exception as e:
            self.logger.error(f"Failed to get executions: {e}")
            raise RuntimeError(f"IBKR API error: {str(e)}")

    @rate_limit(calls_per_second=0.5)
    async def get_market_data(
        self,
        symbol: str,
        sec_type: str,
        exchange: str,
        currency: str = "USD",
        last_trade_date: Optional[str] = None,
        local_symbol: Optional[str] = None,
    ) -> Dict:
        """Get snapshot market data (bid/ask/last/volume) for a contract."""
        try:
            if not await self._ensure_connected():
                raise ConnectionError("Not connected to IBKR")

            contract = self._build_contract(
                symbol=symbol, sec_type=sec_type, exchange=exchange,
                currency=currency, last_trade_date=last_trade_date,
                local_symbol=local_symbol,
            )
            await self.ib.qualifyContractsAsync(contract)
            if not contract.conId:
                raise ValidationError(f"Could not qualify contract: {symbol}")

            ticker = self.ib.reqMktData(contract, '', True, False)
            try:
                # Wait for snapshot data
                await asyncio.sleep(2)

                return {
                    "symbol": contract.symbol,
                    "local_symbol": contract.localSymbol,
                    "con_id": contract.conId,
                    "last": safe_float(ticker.last),
                    "bid": safe_float(ticker.bid),
                    "ask": safe_float(ticker.ask),
                    "high": safe_float(ticker.high),
                    "low": safe_float(ticker.low),
                    "open": safe_float(ticker.open),
                    "close": safe_float(ticker.close),
                    "volume": safe_float(ticker.volume),
                    "time": str(ticker.time) if ticker.time else None,
                }
            finally:
                self.ib.cancelMktData(contract)

        except ValidationError:
            raise
        except Exception as e:
            self.logger.error(f"Failed to get market data for {symbol}: {e}")
            raise RuntimeError(f"IBKR API error: {str(e)}")

    @rate_limit(calls_per_second=0.2)
    async def get_historical_bars(
        self,
        symbol: str,
        sec_type: str,
        exchange: str,
        duration: str = "1 M",
        bar_size: str = "1 day",
        what_to_show: str = "TRADES",
        use_rth: bool = False,
        currency: str = "USD",
        last_trade_date: Optional[str] = None,
        local_symbol: Optional[str] = None,
    ) -> Dict:
        """Get historical OHLCV bars.

        Args:
            duration: Time span, e.g. "1 D", "1 W", "1 M", "3 M", "1 Y".
            bar_size: Bar size, e.g. "1 min", "5 mins", "1 hour", "1 day".
            what_to_show: Data type: TRADES, MIDPOINT, BID, ASK.
            use_rth: True = regular trading hours only.
        """
        try:
            if not await self._ensure_connected():
                raise ConnectionError("Not connected to IBKR")

            contract = self._build_contract(
                symbol=symbol, sec_type=sec_type, exchange=exchange,
                currency=currency, last_trade_date=last_trade_date,
                local_symbol=local_symbol,
            )
            await self.ib.qualifyContractsAsync(contract)
            if not contract.conId:
                raise ValidationError(f"Could not qualify contract: {symbol}")

            bars = await self.ib.reqHistoricalDataAsync(
                contract,
                endDateTime='',
                durationStr=duration,
                barSizeSetting=bar_size,
                whatToShow=what_to_show,
                useRTH=use_rth,
            )

            bar_list = []
            for bar in bars:
                bar_list.append({
                    "date": str(bar.date),
                    "open": safe_float(bar.open),
                    "high": safe_float(bar.high),
                    "low": safe_float(bar.low),
                    "close": safe_float(bar.close),
                    "volume": safe_float(bar.volume),
                    "average": safe_float(bar.average),
                    "bar_count": bar.barCount,
                })

            return {
                "symbol": contract.symbol,
                "local_symbol": contract.localSymbol,
                "bar_count": len(bar_list),
                "bar_size": bar_size,
                "duration": duration,
                "bars": bar_list,
            }

        except ValidationError:
            raise
        except Exception as e:
            self.logger.error(f"Failed to get historical bars for {symbol}: {e}")
            raise RuntimeError(f"IBKR API error: {str(e)}")

    async def what_if_order(
        self,
        symbol: str,
        sec_type: str,
        exchange: str,
        action: str,
        quantity: float,
        order_type: str,
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
        currency: str = "USD",
        last_trade_date: Optional[str] = None,
        local_symbol: Optional[str] = None,
    ) -> Dict:
        """Preview margin impact of an order without placing it."""
        try:
            if not await self._ensure_connected():
                raise ConnectionError("Not connected to IBKR")

            contract = self._build_contract(
                symbol=symbol, sec_type=sec_type, exchange=exchange,
                currency=currency, last_trade_date=last_trade_date,
                local_symbol=local_symbol,
            )
            await self.ib.qualifyContractsAsync(contract)
            if not contract.conId:
                raise ValidationError(f"Could not qualify contract: {symbol}")

            order = self._build_order(
                action=action, quantity=quantity, order_type=order_type,
                limit_price=limit_price, stop_price=stop_price,
            )
            order.whatIf = True

            state = await self.ib.whatIfOrderAsync(contract, order)

            return {
                "symbol": contract.symbol,
                "local_symbol": contract.localSymbol,
                "action": action,
                "quantity": quantity,
                "init_margin_before": state.initMarginBefore,
                "maint_margin_before": state.maintMarginBefore,
                "init_margin_change": state.initMarginChange,
                "maint_margin_change": state.maintMarginChange,
                "init_margin_after": state.initMarginAfter,
                "maint_margin_after": state.maintMarginAfter,
                "equity_with_loan_before": state.equityWithLoanBefore,
                "equity_with_loan_change": state.equityWithLoanChange,
                "equity_with_loan_after": state.equityWithLoanAfter,
                "commission": safe_float(state.commission) if state.commission < 1e300 else None,
                "min_commission": safe_float(state.minCommission) if state.minCommission < 1e300 else None,
                "max_commission": safe_float(state.maxCommission) if state.maxCommission < 1e300 else None,
                "warning_text": state.warningText or None,
            }

        except ValidationError:
            raise
        except Exception as e:
            self.logger.error(f"Failed what-if order for {symbol}: {e}")
            raise RuntimeError(f"IBKR API error: {str(e)}")

    @rate_limit(calls_per_second=0.5)
    async def get_contract_details(
        self,
        symbol: str,
        sec_type: str,
        exchange: str = "",
        currency: str = "USD",
        last_trade_date: Optional[str] = None,
        local_symbol: Optional[str] = None,
    ) -> List[Dict]:
        """Look up contract details: tick size, multiplier, trading hours, etc."""
        try:
            if not await self._ensure_connected():
                raise ConnectionError("Not connected to IBKR")

            contract = self._build_contract(
                symbol=symbol, sec_type=sec_type, exchange=exchange,
                currency=currency, last_trade_date=last_trade_date,
                local_symbol=local_symbol,
            )

            details_list = await self.ib.reqContractDetailsAsync(contract)
            if not details_list:
                raise ValidationError(f"No contract details found for {symbol}")

            results = []
            for d in details_list:
                results.append({
                    "symbol": d.contract.symbol,
                    "local_symbol": d.contract.localSymbol,
                    "sec_type": d.contract.secType,
                    "exchange": d.contract.exchange,
                    "primary_exchange": d.contract.primaryExchange,
                    "currency": d.contract.currency,
                    "con_id": d.contract.conId,
                    "multiplier": d.contract.multiplier,
                    "last_trade_date": d.contract.lastTradeDateOrContractMonth,
                    "min_tick": d.minTick,
                    "long_name": d.longName,
                    "contract_month": d.contractMonth,
                    "trading_hours": d.tradingHours,
                    "liquid_hours": d.liquidHours,
                    "market_name": d.marketName,
                    "order_types": d.orderTypes,
                    "valid_exchanges": d.validExchanges,
                })
            return results

        except ValidationError:
            raise
        except Exception as e:
            self.logger.error(f"Failed to get contract details for {symbol}: {e}")
            raise RuntimeError(f"IBKR API error: {str(e)}")

    async def cancel_all_orders(self) -> Dict:
        """Cancel all open orders."""
        try:
            if not await self._ensure_connected():
                raise ConnectionError("Not connected to IBKR")

            trades = self.ib.openTrades()
            cancelled = []

            for trade in trades:
                try:
                    self.ib.cancelOrder(trade.order)
                    cancelled.append(trade.order.orderId)
                except Exception as e:
                    self.logger.warning(f"Failed to cancel order {trade.order.orderId}: {e}")

            await asyncio.sleep(0.5)

            return {
                "success": True,
                "cancelled_count": len(cancelled),
                "cancelled_order_ids": cancelled,
                "message": f"Cancelled {len(cancelled)} orders",
            }

        except Exception as e:
            self.logger.error(f"Failed to cancel all orders: {e}")
            raise RuntimeError(f"IBKR API error: {str(e)}")

    async def modify_order(
        self,
        order_id: int,
        quantity: Optional[float] = None,
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
        tif: Optional[str] = None,
    ) -> Dict:
        """Modify an existing open order."""
        try:
            if not await self._ensure_connected():
                raise ConnectionError("Not connected to IBKR")

            existing_trade = self._find_trade_by_order_id(order_id)
            if not existing_trade:
                raise ValidationError(f"No open order found with ID {order_id}")

            order = existing_trade.order

            if quantity is not None:
                if quantity > settings.max_order_size:
                    raise TradingError(
                        f"Order size {quantity} exceeds max allowed {settings.max_order_size}"
                    )
                order.totalQuantity = quantity
            if limit_price is not None:
                order.lmtPrice = limit_price
            if stop_price is not None:
                order.auxPrice = stop_price
            if tif is not None:
                order.tif = tif

            updated_trade = self.ib.placeOrder(existing_trade.contract, order)
            await asyncio.sleep(0.5)

            return self._serialize_trade(updated_trade)

        except (TradingError, ValidationError):
            raise
        except Exception as e:
            self.logger.error(f"Failed to modify order {order_id}: {e}")
            raise RuntimeError(f"IBKR API error: {str(e)}")

    # ── Helpers ───────────────────────────────────────────────────────

    def _build_contract(
        self,
        symbol: str,
        sec_type: str,
        exchange: str,
        currency: str = "USD",
        last_trade_date: Optional[str] = None,
        local_symbol: Optional[str] = None,
    ):
        """Build an ib_async contract object."""
        if sec_type.upper() == "FUT":
            contract = Future(
                symbol=symbol,
                exchange=exchange,
                currency=currency,
            )
            if local_symbol:
                contract.localSymbol = local_symbol
            elif last_trade_date:
                contract.lastTradeDateOrContractMonth = last_trade_date
        else:
            contract = Stock(symbol, exchange or "SMART", currency)
        return contract

    def _build_order(
        self,
        action: str,
        quantity: float,
        order_type: str,
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
        trail_stop_price: Optional[float] = None,
        trail_amount: Optional[float] = None,
        trail_percent: Optional[float] = None,
        tif: str = "GTC",
        outside_rth: bool = False,
        parent_id: Optional[int] = None,
        oca_group: Optional[str] = None,
        transmit: bool = True,
        account: Optional[str] = None,
    ) -> Order:
        """Build an ib_async Order object."""
        order_type = order_type.upper()

        if order_type == "MKT":
            order = MarketOrder(action, quantity)
        elif order_type == "LMT":
            if limit_price is None:
                raise ValidationError("limit_price required for LMT orders")
            order = LimitOrder(action, quantity, limit_price)
        elif order_type == "STP":
            if stop_price is None:
                raise ValidationError("stop_price required for STP orders")
            order = StopOrder(action, quantity, stop_price)
        elif order_type in ("STP LMT", "STP_LMT", "STPLMT"):
            if stop_price is None or limit_price is None:
                raise ValidationError(
                    "Both stop_price and limit_price required for STP LMT orders"
                )
            order = Order(
                action=action,
                totalQuantity=quantity,
                orderType="STP LMT",
                auxPrice=stop_price,
                lmtPrice=limit_price,
            )
        elif order_type == "TRAIL":
            if trail_amount is None and trail_percent is None:
                raise ValidationError(
                    "Either trail_amount or trail_percent required for TRAIL orders"
                )
            order = Order(
                action=action,
                totalQuantity=quantity,
                orderType="TRAIL",
            )
            if trail_amount is not None:
                order.auxPrice = trail_amount
            if trail_percent is not None:
                order.trailingPercent = trail_percent
            if trail_stop_price is not None:
                order.trailStopPrice = trail_stop_price
        else:
            raise ValidationError(
                f"Unsupported order type: {order_type}. Use MKT, LMT, STP, STP LMT, or TRAIL."
            )

        order.tif = tif
        order.outsideRth = outside_rth
        order.transmit = transmit
        if parent_id is not None:
            order.parentId = parent_id
        if oca_group is not None:
            order.ocaGroup = oca_group
            order.ocaType = 1  # Cancel remaining on fill
        if account:
            order.account = account

        return order

    def _find_trade_by_order_id(self, order_id: int) -> Optional[Trade]:
        """Find an open trade by order ID."""
        for trade in self.ib.openTrades():
            if trade.order.orderId == order_id:
                return trade
        return None

    def _serialize_trade(self, trade: Trade) -> Dict:
        """Convert a Trade object to a serializable dict."""
        order = trade.order
        contract = trade.contract
        status = trade.orderStatus

        result = {
            "order_id": order.orderId,
            "perm_id": order.permId,
            "action": order.action,
            "quantity": safe_float(order.totalQuantity),
            "order_type": order.orderType,
            "limit_price": safe_float(order.lmtPrice) if order.lmtPrice < 1e300 else None,
            "stop_price": safe_float(order.auxPrice) if order.auxPrice < 1e300 else None,
            "tif": order.tif,
            "status": status.status,
            "filled": safe_float(status.filled),
            "remaining": safe_float(status.remaining),
            "avg_fill_price": safe_float(status.avgFillPrice),
            "contract": {
                "symbol": contract.symbol,
                "sec_type": contract.secType,
                "exchange": contract.exchange,
                "currency": contract.currency,
                "local_symbol": contract.localSymbol,
                "con_id": contract.conId,
            },
            "account": order.account,
            "outside_rth": order.outsideRth,
        }

        # Bracket / OCA fields
        if order.parentId:
            result["parent_id"] = order.parentId
        if order.ocaGroup:
            result["oca_group"] = order.ocaGroup

        # Trailing stop fields
        if order.orderType == "TRAIL":
            if order.trailStopPrice < 1e300:
                result["trail_stop_price"] = safe_float(order.trailStopPrice)
            if order.trailingPercent < 1e300:
                result["trail_percent"] = safe_float(order.trailingPercent)

        return result

    def _serialize_position(self, position) -> Dict:
        """Convert Position to serializable dict."""
        return {
            "symbol": position.contract.symbol,
            "secType": position.contract.secType,
            "exchange": position.contract.exchange,
            "position": safe_float(position.position),
            "avgCost": safe_float(position.avgCost),
            "marketPrice": safe_float(getattr(position, 'marketPrice', 0)),
            "marketValue": safe_float(getattr(position, 'marketValue', 0)),
            "unrealizedPNL": safe_float(getattr(position, 'unrealizedPNL', 0)),
            "realizedPNL": safe_float(getattr(position, 'realizedPNL', 0)),
            "account": position.account
        }
    
    def _serialize_account_value(self, account_value) -> Dict:
        """Convert AccountValue to serializable dict."""
        return {
            "tag": account_value.tag,
            "value": account_value.value,
            "currency": account_value.currency,
            "account": account_value.account
        }


# Global client instance
ibkr_client = IBKRClient()
