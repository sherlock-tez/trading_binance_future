import asyncio

from src.config import load_settings
from src.data.binance_feed import CandleEvent
from src.notify.telegram import TelegramNotifier, escape_html
from src.runtime.live_runner import (
    LiveTradingRunner,
    _direction_icon,
    _pnl_icon,
)


def test_escape_html_neutralizes_markup():
    assert escape_html("<b>x</b> & y") == "&lt;b&gt;x&lt;/b&gt; &amp; y"
    # quotes are left intact (quote=False) so prices/reasons read naturally
    assert escape_html('it\'s "fine"') == 'it\'s "fine"'
    assert escape_html(1.2345) == "1.2345"


def test_pnl_icon_by_sign():
    assert _pnl_icon(10.0) == "🟢"
    assert _pnl_icon(-10.0) == "🔴"
    assert _pnl_icon(0.0) == "⚪"


def test_direction_icon_variants():
    assert _direction_icon("LONG") == "🟢⬆️"
    assert _direction_icon("buy") == "🟢⬆️"
    assert _direction_icon("SHORT") == "🔴⬇️"
    assert _direction_icon(" sell ") == "🔴⬇️"
    assert _direction_icon("") == "⚪"
    assert _direction_icon("unknown") == "⚪"


def test_notifier_disabled_does_not_post(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "src.notify.telegram.requests.post",
        lambda *a, **k: calls.append((a, k)),
    )
    TelegramNotifier(token="", chat_id="").send("hello")
    assert calls == []


def test_notifier_sends_html_parse_mode(monkeypatch):
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return None

    monkeypatch.setattr("src.notify.telegram.requests.post", fake_post)

    TelegramNotifier(token="tok", chat_id="chat").send("🚀 <b>hi</b>")

    assert "/bottok/sendMessage" in captured["url"]
    assert captured["json"]["parse_mode"] == "HTML"
    assert captured["json"]["chat_id"] == "chat"
    assert captured["json"]["text"] == "🚀 <b>hi</b>"


def _runner_with_settings():
    runner = LiveTradingRunner.__new__(LiveTradingRunner)
    runner.settings = load_settings()
    return runner


def test_lifecycle_message_has_icons_and_bold():
    runner = _runner_with_settings()
    snapshot = {
        "wallet_balance": 1000.0,
        "available_balance": 800.0,
        "unrealized_pnl": -12.5,
        "equity": 987.5,
        "assets": [
            {
                "asset": "USDC",
                "wallet_balance": 1000.0,
                "available_balance": 800.0,
                "unrealized_pnl": -12.5,
                "equity": 987.5,
            }
        ],
    }

    msg = runner._format_lifecycle_message("STARTING", snapshot=snapshot)

    assert "🟢 <b>Bot STARTING</b>" in msg
    assert "💰 <b>Account</b>" in msg
    assert "🔴 Unrealized PnL: <code>-12.5000</code>" in msg
    assert "📦 <b>Assets</b>" in msg
    assert "<b>USDC</b>" in msg


class _RecordingNotifier:
    def __init__(self):
        self.messages = []

    def send(self, message: str) -> None:
        self.messages.append(message)


class _ExplodingDataService:
    def refresh_symbol_timeframes(self, *a, **k):
        raise RuntimeError("binance refresh boom <x>")


def test_closed_candle_failure_alerts_telegram_and_does_not_raise():
    runner = LiveTradingRunner.__new__(LiveTradingRunner)
    runner.settings = load_settings()
    runner.notifier = _RecordingNotifier()
    runner.data_service = _ExplodingDataService()

    event = CandleEvent(
        symbol="SOLUSDC",
        timeframe="1h",
        open_time=1,
        close_time=2,
        open_price=1.0,
        high_price=1.0,
        low_price=1.0,
        close_price=1.0,
        volume=1.0,
    )

    # must not propagate — a bad candle should never kill the stream loop
    asyncio.run(runner._on_closed_candle(event))

    assert len(runner.notifier.messages) == 1
    alert = runner.notifier.messages[0]
    assert "⚠️ <b>Signal cycle error</b>" in alert
    assert "SOLUSDC" in alert
    assert "RuntimeError" in alert
    # error text is HTML-escaped, not raw
    assert "binance refresh boom &lt;x&gt;" in alert
    assert "<x>" not in alert


def test_lifecycle_message_escapes_error_text():
    runner = _runner_with_settings()

    msg = runner._format_lifecycle_message("STOPPED", error="boom <script> & co")

    assert "🔴 <b>Bot STOPPED</b>" in msg
    assert "boom &lt;script&gt; &amp; co" in msg
    # raw markup must never leak into the message
    assert "<script>" not in msg
