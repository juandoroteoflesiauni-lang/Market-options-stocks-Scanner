"""
backend/services/notification_service.py
════════════════════════════════════════════════════════════════════════════════
Centralized Notification Service for QuantumAnalyzer.
Supports internal logging, and can be extended for Telegram/Slack/Webhooks.
════════════════════════════════════════════════════════════════════════════════
"""

from enum import Enum
from typing import Any

try:
    from config.logger_setup import get_logger
except ModuleNotFoundError:
    from backend.config.logger_setup import get_logger

logger = get_logger(__name__)


class AlertLevel(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    VETO = "veto"  # Specialized level for Probabilistic Veto


class NotificationService:
    """Dispatches alerts across multiple channels."""

    def __init__(self):
        self.logger = logger
        import os

        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.is_enabled = bool(self.bot_token and self.chat_id)

    async def broadcast_alert(
        self,
        title: str,
        message: str,
        level: AlertLevel = AlertLevel.INFO,
        metadata: dict[str, Any] | None = None,
    ):
        """
        Broadcasts an alert to all configured channels.
        Current implementation: Logging + Telegram.
        """
        log_msg = f"[{level.upper()}] {title}: {message}"
        if metadata:
            log_msg += f" | Metadata: {metadata}"

        if level == AlertLevel.CRITICAL or level == AlertLevel.VETO:
            self.logger.error(log_msg)
        elif level == AlertLevel.WARNING:
            self.logger.warning(log_msg)
        else:
            self.logger.info(log_msg)

        # Telegram Integration
        if self.is_enabled:
            await self._send_to_telegram(title, message, level, metadata)

    async def _send_to_telegram(
        self, title: str, message: str, level: AlertLevel, metadata: dict[str, Any] | None = None
    ):
        """Sends a formatted message to Telegram."""
        import httpx

        icon = "ℹ️"
        if level == AlertLevel.WARNING:
            icon = "⚠️"
        if level == AlertLevel.CRITICAL:
            icon = "🚨"
        if level == AlertLevel.VETO:
            icon = "⛔"

        tg_message = f"{icon} <b>{title}</b>\n\n{message}\n"

        if metadata:
            tg_message += "\n<b>Metrics:</b>\n"
            for k, v in metadata.items():
                tg_message += f"• {k}: <code>{v}</code>\n"

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": tg_message, "parse_mode": "HTML"}

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, json=payload, timeout=5.0)
                if resp.status_code != 200:
                    self.logger.warning(f"Telegram notification failed: {resp.text}")
        except Exception as e:
            self.logger.error(f"Error sending Telegram notification: {e}")

    async def notify_veto(self, symbol: str, reason: str, metrics: dict[str, Any]):
        """Specialized notification for Probabilistic Veto."""
        title = f"INSTITUTIONAL VETO: {symbol}"
        message = "Inference engine has <b>BLOCKED</b> operations due to high institutional risk."

        # Enrich metrics with the reason
        enriched_metrics = {"Reason": reason}
        enriched_metrics.update(metrics)

        await self.broadcast_alert(title, message, level=AlertLevel.VETO, metadata=enriched_metrics)


# Global Instance
notification_service = NotificationService()
