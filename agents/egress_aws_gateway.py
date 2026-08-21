"""Groundwork AWS API Gateway IP Fanout (Egress Layer).

Uses the ``requests-ip-rotator`` library to create ephemeral API Gateway
endpoints that rotate IP addresses on every request.  Each HTTP call
exits through a different AWS edge IP.

Falls back gracefully when boto3 or AWS credentials are not configured.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


class AWSGatewayFanout:
    """Uses requests-ip-rotator for serverless IP rotation via AWS API Gateway."""

    def __init__(self, target_host: str = "https://gworky.com") -> None:
        self.target_host = target_host
        self._gateway: Any = None

    def create_gateway(self) -> bool:
        """Create an API Gateway for the target host.

        Requires ``pip install requests-ip-rotator`` and valid AWS credentials
        (``AWS_ACCESS_KEY_ID`` + ``AWS_SECRET_ACCESS_KEY``).
        """
        try:
            from requests_ip_rotator import ApiGateway
        except ImportError:
            logger.info("requests-ip-rotator not installed — AWS fanout unavailable")
            return False

        if not os.environ.get("AWS_ACCESS_KEY_ID"):
            logger.info("AWS credentials not configured — AWS fanout unavailable")
            return False

        try:
            self._gateway = ApiGateway(self.target_host)
            self._gateway.start()
            logger.info("AWS API Gateway created for %s", self.target_host)
            return True
        except Exception as exc:
            logger.warning("Failed to create AWS API Gateway: %s", exc)
            self._gateway = None
            return False

    def get_session(self) -> Any:
        """Return a requests.Session wired through the API Gateway.

        Each request through this session exits with a different AWS edge IP.
        """
        if not self._gateway:
            return None
        try:
            import requests

            session = requests.Session()
            session.mount(self.target_host, self._gateway)
            return session
        except Exception as exc:
            logger.warning("Failed to create gateway session: %s", exc)
            return None

    def send_request(self, url: str) -> dict[str, Any]:
        """Send a single request through the gateway and return response info."""
        session = self.get_session()
        if not session:
            return {"success": False, "error": "no_gateway_session"}
        try:
            resp = session.get(url, timeout=15)
            return {
                "success": True,
                "status_code": resp.status_code,
                "url": url,
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)[:200]}

    def cleanup(self) -> None:
        """Tear down the API Gateway to avoid orphaned resources."""
        if self._gateway:
            try:
                self._gateway.shutdown()
                logger.info("AWS API Gateway shut down")
            except Exception as exc:
                logger.warning("Failed to shut down AWS API Gateway: %s", exc)
            self._gateway = None

    @staticmethod
    def is_available() -> bool:
        """Check if requests-ip-rotator and AWS credentials are configured."""
        try:
            import requests_ip_rotator  # noqa: F401
        except ImportError:
            return False
        return bool(os.environ.get("AWS_ACCESS_KEY_ID"))

    @classmethod
    def health_check(cls) -> dict[str, Any]:
        """Check AWS fanout availability and credential status."""
        result: dict[str, Any] = {
            "name": "aws_gateway_fanout",
            "available": False,
            "has_library": False,
            "has_credentials": False,
            "error": None,
        }
        try:
            import requests_ip_rotator  # noqa: F401

            result["has_library"] = True
        except ImportError:
            result["error"] = "requests-ip-rotator not installed"
            return result

        result["has_credentials"] = bool(os.environ.get("AWS_ACCESS_KEY_ID"))
        result["available"] = result["has_library"] and result["has_credentials"]

        if not result["has_credentials"]:
            result["error"] = "AWS_ACCESS_KEY_ID not set"

        return result
