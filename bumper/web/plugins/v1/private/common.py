"""Common plugin module."""
import json
import logging
import os
from collections.abc import Iterable

from aiohttp import web
from aiohttp.web_exceptions import HTTPInternalServerError
from aiohttp.web_request import Request
from aiohttp.web_response import Response
from aiohttp.web_routedef import AbstractRouteDef

from bumper.util import get_current_time_as_millis

from ... import WebserverPlugin, get_success_response
from . import BASE_URL


class CommonPlugin(WebserverPlugin):
    """Common plugin."""

    @property
    def routes(self) -> Iterable[AbstractRouteDef]:
        """Plugin routes."""
        return [
            web.route(
                "*",
                f"{BASE_URL}common/checkAPPVersion",
                _handle_check_app_version,
            ),
            web.route(
                "*",
                f"{BASE_URL}common/checkVersion",
                _handle_check_version,
            ),
            web.route(
                "*",
                f"{BASE_URL}common/uploadDeviceInfo",
                _handle_upload_device_info,
            ),
            web.route(
                "*",
                f"{BASE_URL}common/getSystemReminder",
                _handle_get_system_reminder,
            ),
            web.route(
                "*",
                f"{BASE_URL}common/getConfig",
                _handle_get_config,
            ),
            web.route(
                "*",
                f"{BASE_URL}common/getAreas",
                _handle_get_areas,
            ),
            web.route(
                "*",
                f"{BASE_URL}common/getAgreementURLBatch",
                _handle_get_agreement_url_batch,
            ),
            web.route(
                "*",
                f"{BASE_URL}common/getTimestamp",
                _handle_get_timestamp,
            ),
        ]


async def _handle_check_version(_: Request) -> Response:
    return get_success_response(
        {
            "c": None,
            "img": None,
            "r": 0,
            "t": None,
            "u": None,
            "ut": 0,
            "v": None,
        }
    )


async def _handle_check_app_version(_: Request) -> Response:
    return get_success_response(
        {
            "c": None,
            "downPageUrl": None,
            "img": None,
            "nextAlertTime": None,
            "r": 0,
            "t": None,
            "u": None,
            "ut": 0,
            "v": None,
        }
    )


async def _handle_upload_device_info(_: Request) -> Response:
    return get_success_response(None)


async def _handle_get_system_reminder(_: Request) -> Response:
    return get_success_response(
        {
            "iosGradeTime": {"iodGradeFlag": "N"},
            "openNotification": {
                "openNotificationContent": None,
                "openNotificationFlag": "N",
                "openNotificationTitle": None,
            },
        }
    )


async def _handle_get_config(request: Request) -> Response:
    try:
        data = []
        for key in request.query["keys"].split(","):
            data.append({"key": key, "value": "{\"publicKey\":\"public key or something\"}"})

        return get_success_response(data)
    except Exception:  # pylint: disable=broad-except
        logging.error("Unexpected exception occurred", exc_info=True)

    raise HTTPInternalServerError


async def _handle_get_areas(_: Request) -> Response:
    try:
        with open(
            os.path.join(os.path.dirname(__file__), "common_area.json"),
            encoding="utf-8",
        ) as file:
            return get_success_response(json.load(file))
    except Exception:  # pylint: disable=broad-except
        logging.error("Unexpected exception occurred", exc_info=True)

    raise HTTPInternalServerError


async def _handle_get_agreement_url_batch(_: Request) -> Response:
    return get_success_response(
        [
            {
                "acceptTime": None,
                "force": None,
                "id": "20180804040641_7d746faf18b8cb22a50d145598fe4c90",
                "type": "USER",
                "url": "https://gl-eu-wap.ecovacs.com/content/agreement?id=20180804040641_7d746faf18b8cb22a50d145598fe4c90&language=EN",
                "version": "1.03",
            },
            {
                "acceptTime": None,
                "force": None,
                "id": "20180804040245_4e7c56dfb7ebd3b81b1f2747d0859fac",
                "type": "PRIVACY",
                "url": "https://gl-eu-wap.ecovacs.com/content/agreement?id=20180804040245_4e7c56dfb7ebd3b81b1f2747d0859fac&language=EN",
                "version": "1.03",
            },
        ]
    )


async def _handle_get_timestamp(_: Request) -> Response:
    return get_success_response({"timestamp": get_current_time_as_millis()})
