from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import List

from dotenv import load_dotenv
import yaml


@dataclass(frozen=True)
class Settings:
    binance_futures_api_key: str
    binance_futures_api_secret: str
    telegram_bot_token: str
    telegram_chat_id: str
    binance_testnet: bool

    symbols: List[str]
    signal_timeframe: str
    sup_res_timeframes: List[str]

    leverage: int
    max_open_positions: int
    position_equity_ratio: float
    maker_only: bool

    rsi_period: int
    macd_fast: int
    macd_slow: int
    macd_signal: int
    divergence_lookback: int
    pivot_window: int

    order_reprice_bps: float
    order_reprice_max_retries: int
    stop_loss_buffer_bps: float
    take_profit_buffer_bps: float

    backtest_month_windows: List[int]
    initial_balance: float
    maker_fee_rate: float

    log_level: str

    # Optional strategy enhancements (defaults preserve legacy behavior).
    atr_period: int = 14
    use_atr_stops: bool = False
    atr_sl_mult: float = 1.5
    atr_tp_mult: float = 3.0
    use_trend_filter: bool = False
    trend_ema_period: int = 200
    min_rr_ratio: float = 0.0
    max_sl_distance_pct: float = 0.0
    rsi_long_max: float = 50.0
    rsi_short_min: float = 50.0
    require_macd_divergence: bool = True
    strategy_version: str = "v1"
    backtest_history_dir: str = "backtest_history"


class ConfigError(ValueError):
    pass


def _parse_bool(value: str | bool | None, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "y", "on"}:
        return True
    if lowered in {"0", "false", "no", "n", "off"}:
        return False
    raise ConfigError(f"Invalid boolean value: {value}")


def _as_str_list(value: Any, *, default: List[str] | None = None, uppercase: bool = False) -> List[str]:
    if value is None:
        return list(default or [])
    if isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
    elif isinstance(value, str):
        items = [item.strip() for item in value.split(",") if item.strip()]
    else:
        raise ConfigError(f"Expected list or csv string, got: {type(value)}")
    if uppercase:
        return [item.upper() for item in items]
    return items


def _as_int_list(value: Any, *, default: List[int] | None = None) -> List[int]:
    if value is None:
        return list(default or [])
    if isinstance(value, list):
        return [int(item) for item in value]
    if isinstance(value, str):
        return [int(item.strip()) for item in value.split(",") if item.strip()]
    raise ConfigError(f"Expected list or csv string for int list, got: {type(value)}")


def _read_yaml_config(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        raise ConfigError(f"Missing config file: {config_path}")
    with config_path.open("r", encoding="utf-8") as file:
        parsed = yaml.safe_load(file) or {}
    if not isinstance(parsed, dict):
        raise ConfigError("config.yaml must contain a mapping at the root")
    return parsed


def _section(cfg: dict[str, Any], key: str) -> dict[str, Any]:
    value = cfg.get(key, {})
    if not isinstance(value, dict):
        raise ConfigError(f"Section '{key}' in config.yaml must be a mapping")
    return value


def _resolve_config_path(root: Path, symbol: str | None) -> Path:
    """Pick `{symbol}_config.yaml` when a symbol is given, falling back to `config.yaml`.

    Symbol matching is case-insensitive. If the per-symbol file is missing, raise so
    callers don't silently fall through to a global default that has the wrong symbol.
    """
    if symbol is None:
        return root / "config.yaml"
    candidate = root / f"{symbol.strip().lower()}_config.yaml"
    if not candidate.exists():
        raise ConfigError(f"Missing per-symbol config file: {candidate.name}")
    return candidate


def load_settings(symbol: str | None = None) -> Settings:
    load_dotenv()
    root = Path(__file__).resolve().parents[1]
    cfg = _read_yaml_config(_resolve_config_path(root, symbol))

    binance_cfg = _section(cfg, "binance")
    trading_cfg = _section(cfg, "trading")
    strategy_cfg = _section(cfg, "strategy")
    execution_cfg = _section(cfg, "execution")
    backtest_cfg = _section(cfg, "backtest")
    runtime_cfg = _section(cfg, "runtime")

    symbols = _as_str_list(
        trading_cfg.get("symbols"),
        default=["BTCUSDT", "BTCUSDC", "ETHUSDT", "ETHUSDC"],
        uppercase=True,
    )
    if not symbols:
        raise ConfigError("SYMBOLS must not be empty")

    sup_res_timeframes = _as_str_list(
        trading_cfg.get("sup_res_timeframes"),
        default=["3H", "6H", "12H", "1D", "1W"],
        uppercase=True,
    )
    sup_res_timeframes = [value.lower() for value in sup_res_timeframes]

    windows = _as_int_list(backtest_cfg.get("month_windows"), default=[3, 6, 12, 15])
    if not windows:
        raise ConfigError("BACKTEST_MONTH_WINDOWS must not be empty")

    settings = Settings(
        binance_futures_api_key=os.getenv("BINANCE_FUTURES_API_KEY", ""),
        binance_futures_api_secret=os.getenv("BINANCE_FUTURES_API_SECRET", ""),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        binance_testnet=_parse_bool(binance_cfg.get("testnet"), default=True),
        symbols=symbols,
        signal_timeframe=str(trading_cfg.get("signal_timeframe", "1h")).strip().lower(),
        sup_res_timeframes=sup_res_timeframes,
        leverage=int(trading_cfg.get("leverage", 10)),
        max_open_positions=int(trading_cfg.get("max_open_positions", 1)),
        position_equity_ratio=float(trading_cfg.get("position_equity_ratio", 0.95)),
        maker_only=_parse_bool(trading_cfg.get("maker_only"), default=True),
        rsi_period=int(strategy_cfg.get("rsi_period", 14)),
        macd_fast=int(strategy_cfg.get("macd_fast", 12)),
        macd_slow=int(strategy_cfg.get("macd_slow", 26)),
        macd_signal=int(strategy_cfg.get("macd_signal", 9)),
        divergence_lookback=int(strategy_cfg.get("divergence_lookback", 80)),
        pivot_window=int(strategy_cfg.get("pivot_window", 3)),
        order_reprice_bps=float(execution_cfg.get("order_reprice_bps", 2)),
        order_reprice_max_retries=int(execution_cfg.get("order_reprice_max_retries", 5)),
        stop_loss_buffer_bps=float(execution_cfg.get("stop_loss_buffer_bps", 8)),
        take_profit_buffer_bps=float(execution_cfg.get("take_profit_buffer_bps", 8)),
        backtest_month_windows=windows,
        initial_balance=float(backtest_cfg.get("initial_balance", 10000)),
        maker_fee_rate=float(backtest_cfg.get("maker_fee_rate", 0.0002)),
        log_level=str(runtime_cfg.get("log_level", "INFO")),
        atr_period=int(strategy_cfg.get("atr_period", 14)),
        use_atr_stops=_parse_bool(strategy_cfg.get("use_atr_stops"), default=False),
        atr_sl_mult=float(strategy_cfg.get("atr_sl_mult", 1.5)),
        atr_tp_mult=float(strategy_cfg.get("atr_tp_mult", 3.0)),
        use_trend_filter=_parse_bool(strategy_cfg.get("use_trend_filter"), default=False),
        trend_ema_period=int(strategy_cfg.get("trend_ema_period", 200)),
        min_rr_ratio=float(strategy_cfg.get("min_rr_ratio", 0.0)),
        max_sl_distance_pct=float(strategy_cfg.get("max_sl_distance_pct", 0.0)),
        rsi_long_max=float(strategy_cfg.get("rsi_long_max", 50.0)),
        rsi_short_min=float(strategy_cfg.get("rsi_short_min", 50.0)),
        require_macd_divergence=_parse_bool(strategy_cfg.get("require_macd_divergence"), default=True),
        strategy_version=str(strategy_cfg.get("strategy_version", "v1")).strip(),
        backtest_history_dir=str(backtest_cfg.get("history_dir", "backtest_history")).strip(),
    )

    if settings.leverage <= 0:
        raise ConfigError("LEVERAGE must be > 0")
    if not (0 < settings.position_equity_ratio <= 1):
        raise ConfigError("POSITION_EQUITY_RATIO must be in (0, 1]")
    if settings.max_open_positions <= 0:
        raise ConfigError("MAX_OPEN_POSITIONS must be > 0")

    return settings
