# SPDX-FileCopyrightText: © 2024 Jon Lech Johansen <jon@nanocrew.net>
# SPDX-License-Identifier: Apache-2.0

"""Dahua VCS-SH30 PoE speaker API client."""

import os
import mimetypes
import aiohttp
import aiofiles

from homeassistant.core import HomeAssistant
from homeassistant.helpers import aiohttp_client
from homeassistant.exceptions import HomeAssistantError
from .const import TIMEOUT


VOLUME_KEY = "aoVol"


class ConnectError(HomeAssistantError):
    """Error to indicate we cannot connect."""


class AuthError(HomeAssistantError):
    """Error to indicate there is invalid auth."""


class DahuaClient:
    _headers = None

    def __init__(self, hass: HomeAssistant, host: str, username: str, password: str):
        self._hass = hass
        self._host = host
        self._username = username
        self._password = password

        self._API_TMPL = f"http://{host}/prod-api"
        self._PRG_TMPL = f"{self._API_TMPL}/program"

    @property
    def host(self) -> str:
        return self._host

    async def _handle_error(self, res):
        if res.status != 200:
            raise HomeAssistantError(f"HTTP status {res.status}: {res.reason}")
        else:
            rj = await res.json()
            code = rj.get("code")
            message = rj.get("message")
            if code is not None and code != 200:
                if code == 401 or (code == 400 and "username or password" in message):
                    raise AuthError(message)
                else:
                    raise HomeAssistantError(f"Dahua API error {code}: {message}")

    async def login(self):
        try:
            data = {"username": self._username, "password": self._password}
            session = aiohttp_client.async_get_clientsession(self._hass)
            async with session.post(
                f"{self._API_TMPL}/uer/login", json=data, timeout=TIMEOUT
            ) as res:
                await self._handle_error(res)
                token = (await res.json()).get("data", {}).get("token")
                if token is None:
                    raise HomeAssistantError("No token from Dahua login API")
                self._headers = {"X-Token": token}
        except TimeoutError as err:
            raise ConnectError(
                f"Timeout while logging into Dahua Speaker at {self._host}"
            ) from err
        except aiohttp.client_exceptions.ClientError as err:
            raise ConnectError(
                f"Error while logging into Dahua Speaker at {self._host}"
            ) from err

    async def get_device_info(self) -> dict:
        try:
            session = aiohttp_client.async_get_clientsession(self._hass)
            async with session.get(
                f"{self._API_TMPL}/device/info", headers=self._headers, timeout=TIMEOUT
            ) as res:
                await self._handle_error(res)
                return (await res.json()).get("data", {})
        except TimeoutError as err:
            raise ConnectError(
                f"Timeout while getting device info from Dahua Speaker at {self._host}"
            ) from err
        except aiohttp.client_exceptions.ClientError as err:
            raise ConnectError(
                f"Error while getting device info from Dahua Speaker at {self._host}"
            ) from err

    async def _set_property(self, name: str, value):
        data = {name: value}
        try:
            session = aiohttp_client.async_get_clientsession(self._hass)
            async with session.post(
                f"{self._API_TMPL}/device/edit",
                json=data,
                headers=self._headers,
                timeout=TIMEOUT,
            ) as res:
                await self._handle_error(res)
                return (await res.json()).get(name, value)
        except TimeoutError as err:
            raise ConnectError(
                f"Timeout while setting volume on Dahua Speaker at {self._host}"
            ) from err
        except aiohttp.client_exceptions.ClientError as err:
            raise ConnectError(
                f"Error while setting volume on Dahua Speaker at {self._host}"
            ) from err

    async def set_volume(self, volume: int) -> int:
        return int(await self._set_property(VOLUME_KEY, volume))

    async def fetch_file_list(self) -> dict:
        try:
            session = aiohttp_client.async_get_clientsession(self._hass)
            async with session.get(
                f"{self._PRG_TMPL}/info", headers=self._headers, timeout=TIMEOUT
            ) as res:
                await self._handle_error(res)
                files = (await res.json()).get("data", {}).get("files", [])
                return {file["name"]: file for file in files}
        except TimeoutError as err:
            raise ConnectError(
                f"Timeout while fetching list from Dahua Speaker at {self._host}"
            ) from err
        except aiohttp.client_exceptions.ClientError as err:
            raise ConnectError(
                f"Error while fetching list from Dahua Speaker at {self._host}"
            ) from err

    async def push_file(self, file_path: str, name: str = None):
        if not os.path.exists(file_path):
            raise FileNotFoundError(file_path)

        AUDIO_MPEG = "audio/mpeg"
        mt = mimetypes.guess_type(file_path)
        if mt is None or mt[0] != AUDIO_MPEG:
            raise HomeAssistantError("Dahua Speaker only supports MP3s")

        if name is None:
            name = os.path.basename(file_path)
        async with aiofiles.open(file_path, "rb") as f:
            data = aiohttp.FormData()
            data.add_field("file", f, filename=name, content_type=AUDIO_MPEG)
            try:
                session = aiohttp_client.async_get_clientsession(self._hass)
                async with session.post(
                    f"{self._PRG_TMPL}/upload",
                    data=data,
                    headers=self._headers,
                    timeout=TIMEOUT,
                ) as res:
                    await self._handle_error(res)
            except TimeoutError as err:
                raise ConnectError(
                    f"Timeout while pushing file to Dahua Speaker at {self._host}"
                ) from err
            except aiohttp.client_exceptions.ClientError as err:
                raise ConnectError(
                    f"Error while pushing file to Dahua Speaker at {self._host}"
                ) from err

        files = await self.fetch_file_list()
        if name not in files:
            raise HomeAssistantError(f"File '{name}' is missing on Dahua Speaker")
        return files[name]

    async def play_file(self, file_id):
        try:
            data = {"id": file_id}
            session = aiohttp_client.async_get_clientsession(self._hass)
            async with session.post(
                f"{self._PRG_TMPL}/start",
                json=data,
                headers=self._headers,
                timeout=TIMEOUT,
            ) as res:
                await self._handle_error(res)
        except TimeoutError as err:
            raise ConnectError(
                f"Timeout while playing file on Dahua Speaker at {self._host}"
            ) from err
        except aiohttp.client_exceptions.ClientError as err:
            raise ConnectError(
                f"Error while playing file on Dahua Speaker at {self._host}"
            ) from err
