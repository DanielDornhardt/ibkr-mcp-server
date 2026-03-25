"""Tests for utility functions."""

import math
import pytest

from ibkr_mcp_server.utils import (
    safe_float,
    safe_int,
    validate_symbol,
    validate_symbols,
    format_currency,
    format_percentage,
    IBKRError,
    ConnectionError,
    TradingError,
    ValidationError,
)


class TestSafeFloat:
    """Tests for safe_float — the NaN/Inf fix is critical."""

    def test_normal_float(self):
        assert safe_float(1.5) == 1.5

    def test_integer(self):
        assert safe_float(42) == 42.0

    def test_string_float(self):
        assert safe_float("3.14") == 3.14

    def test_none_returns_default(self):
        assert safe_float(None) == 0.0

    def test_empty_string_returns_default(self):
        assert safe_float("") == 0.0

    def test_nan_returns_default(self):
        """Critical: NaN must not pass through (would produce invalid JSON)."""
        assert safe_float(float("nan")) == 0.0

    def test_inf_returns_default(self):
        """Critical: Inf must not pass through (IB sentinel values)."""
        assert safe_float(float("inf")) == 0.0

    def test_negative_inf_returns_default(self):
        assert safe_float(float("-inf")) == 0.0

    def test_ib_sentinel_value_is_finite(self):
        """IB's sentinel (DBL_MAX ~1.8e308) is finite, not inf.
        safe_float passes it through; sentinel filtering is done
        separately with '< 1e300' guards in serialization code."""
        result = safe_float(1.7976931348623157e308)
        assert result == 1.7976931348623157e308  # passes through — it's finite

    def test_custom_default(self):
        assert safe_float(None, default=-1.0) == -1.0

    def test_invalid_string(self):
        assert safe_float("not_a_number") == 0.0

    def test_zero(self):
        assert safe_float(0.0) == 0.0

    def test_negative(self):
        assert safe_float(-42.5) == -42.5


class TestSafeInt:

    def test_normal_int(self):
        assert safe_int(42) == 42

    def test_string_int(self):
        assert safe_int("42") == 42

    def test_string_float(self):
        assert safe_int("100.0") == 100

    def test_none(self):
        assert safe_int(None) == 0

    def test_invalid(self):
        assert safe_int("abc") == 0


class TestValidateSymbol:

    def test_normal_symbol(self):
        assert validate_symbol("ES") == "ES"

    def test_lowercase_uppercased(self):
        assert validate_symbol("es") == "ES"

    def test_whitespace_stripped(self):
        assert validate_symbol("  ES  ") == "ES"

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            validate_symbol("")

    def test_none_raises(self):
        with pytest.raises(ValueError):
            validate_symbol(None)

    def test_too_long_raises(self):
        with pytest.raises(ValueError):
            validate_symbol("A" * 13)


class TestValidateSymbols:

    def test_single_symbol(self):
        assert validate_symbols("ES") == ["ES"]

    def test_multiple_symbols(self):
        assert validate_symbols("ES,NQ,GC") == ["ES", "NQ", "GC"]

    def test_deduplication(self):
        assert validate_symbols("ES,es,ES") == ["ES"]

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            validate_symbols("")


class TestExceptionHierarchy:

    def test_trading_error_is_ibkr_error(self):
        assert issubclass(TradingError, IBKRError)

    def test_validation_error_is_ibkr_error(self):
        assert issubclass(ValidationError, IBKRError)

    def test_connection_error_is_ibkr_error(self):
        assert issubclass(ConnectionError, IBKRError)
