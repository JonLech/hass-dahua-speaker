# SPDX-FileCopyrightText: © 2024 Jon Lech Johansen <jon@nanocrew.net>
# SPDX-License-Identifier: Apache-2.0

"""Support for Dahua VCS-SH30 PoE speaker."""

import os
import aiohttp
import pathlib
import tempfile
from functools import wraps
from typing import Any, Concatenate
from collections.abc import Awaitable, Callable, Coroutine

from homeassistant.components import media_source
from homeassistant.components.media_player import (
    BrowseMedia,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerDeviceClass,
    MediaPlayerState,
    MediaType,
    async_process_play_media_url,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import (
    DeviceInfo,
    format_mac,
    CONNECTION_NETWORK_MAC,
)
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import aiohttp_client

from . import DahuaSpeakerConfigEntry
from .dahua_client import DahuaClient, ConnectError, AuthError, VOLUME_KEY
from .const import DEFAULT_NAME, DOMAIN, LOGGER


def _media_source_content_filter(item: BrowseMedia) -> bool:
    """Content filter for media sources."""
    if not item.media_content_id.startswith("media-source://"):
        return False
    media_source = item.media_content_id.rsplit("/")[2]
    return media_source in ("media_source", "tts") and item.media_content_type in (
        "app",
        "provider",
        "audio/mpeg",
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DahuaSpeakerConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    # CONF_NAME is only present in imported YAML.
    name = entry.data.get(CONF_NAME) or DEFAULT_NAME
    client = entry.runtime_data.client
    device_info = entry.runtime_data.device_info

    async_add_entities(
        [DahuaSpeaker(entry, client, device_info, name)], update_before_add=True
    )


def catch_dahua_errors[_DahuaSpeakerT: DahuaSpeaker, **_P](
    func: Callable[Concatenate[_DahuaSpeakerT, _P], Awaitable[None]],
) -> Callable[Concatenate[_DahuaSpeakerT, _P], Coroutine[Any, Any, None]]:
    """Catch Dahua errors."""

    @wraps(func)
    async def wrapper(
        self: _DahuaSpeakerT, *args: _P.args, **kwargs: _P.kwargs
    ) -> None:
        """Catch Dahua errors and modify availability."""
        for i in range(2):
            try:
                await func(self, *args, **kwargs)
                break
            except ConnectError as err:
                if isinstance(
                    err.__cause__,
                    aiohttp.client_exceptions.ServerDisconnectedError,
                ):
                    if i != 0:
                        raise
                elif self._attr_available:
                    LOGGER.error("Connection error: %s", err)
                    self._attr_available = False
                    break

    return wrapper


class DahuaSpeaker(MediaPlayerEntity):
    _attr_icon = "mdi:speaker"
    _attr_device_class = MediaPlayerDeviceClass.SPEAKER
    _attr_supported_features = (
        MediaPlayerEntityFeature.PLAY_MEDIA
        | MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.BROWSE_MEDIA
    )
    _attr_has_entity_name = True
    _attr_name = None
    _attr_available = True

    def __init__(
        self,
        config_entry: ConfigEntry,
        client: DahuaClient,
        device_info: dict,
        name: str,
    ) -> None:
        self._client = client
        self._device_info = device_info
        self._entry_id = config_entry.entry_id

        mac_addr = format_mac(device_info["mac"])
        self._attr_unique_id = mac_addr
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, mac_addr)},
            connections={(CONNECTION_NETWORK_MAC, mac_addr)},
            manufacturer="Dahua",
            model=device_info["model"],
            sw_version=device_info["version"],
            name=name,
        )

    @catch_dahua_errors
    async def async_update(self) -> None:
        try:
            files = await self._client.fetch_file_list()
        except AuthError:
            LOGGER.error(
                "Token has expired for '%s', reloading integration", self._client.host
            )
            self.hass.async_create_task(
                self.hass.config_entries.async_reload(self._entry_id)
            )
            return

        self._attr_available = True

        if any([True for f in files.values() if f["playStatus"] == 1]):
            self._attr_state = MediaPlayerState.PLAYING
        else:
            self._attr_state = MediaPlayerState.IDLE

    @property
    def volume_level(self) -> float | None:
        """Volume level of the media player (0..1)."""
        return int(self._device_info[VOLUME_KEY]) / 10

    @catch_dahua_errors
    async def async_set_volume_level(self, volume: float) -> None:
        """Set volume level, range 0..1."""
        new_volume = await self._client.set_volume(round(volume * 10))
        self._device_info[VOLUME_KEY] = new_volume

    @catch_dahua_errors
    async def async_play_media(
        self, media_type: MediaType | str, media_id: str, **kwargs: Any
    ) -> None:
        if not media_source.is_media_source_id(media_id):
            LOGGER.error("Unsupported media_id: %s", media_id)
            return

        source = media_id.split("/")[2]
        is_tts = source == "tts"
        is_ms = source == "media_source"

        sourced_media = await media_source.async_resolve_media(
            self.hass, media_id, self.entity_id
        )
        media_id = sourced_media.url

        name = os.path.basename(media_id)
        file_path = None

        if is_tts:
            tts_cache_dir = self.hass.config.path("tts")
            file_path = os.path.join(tts_cache_dir, name)
        elif is_ms:
            source_dir_id = media_id.split("/")[2]
            if source_dir_id in self.hass.config.media_dirs:
                base_path = self.hass.config.media_dirs[source_dir_id]
                file_path = os.path.join(base_path, name)

        if file_path is None or not os.path.exists(file_path):
            media_id = async_process_play_media_url(self.hass, media_id)

            session = aiohttp_client.async_get_clientsession(self.hass)
            async with session.get(media_id) as res:
                if res.status != 200:
                    raise HomeAssistantError(f"Status {res.status} when fetching file")
                if res.headers.get("Content-Type") != "audio/mpeg":
                    raise HomeAssistantError("Only MP3 files are supported")
                tf = tempfile.NamedTemporaryFile(suffix=".mp3")
                async for chunk in res.content.iter_chunked(16384):
                    tf.write(chunk)
                file_path = tf.name
        elif pathlib.Path(file_path).suffix != ".mp3":
            raise HomeAssistantError("Only MP3 files are supported")

        files = await self._client.fetch_file_list()

        if name not in files:
            LOGGER.debug("Pushing file to speaker: %s", name)
            new_file = await self._client.push_file(file_path, name=name)
            file_id = new_file["id"]
        else:
            file_id = files[name]["id"]

        await self._client.play_file(file_id)

    async def async_browse_media(
        self,
        media_content_type: MediaType | str | None = None,
        media_content_id: str | None = None,
    ) -> BrowseMedia:
        """Implement the websocket media browsing helper."""
        item = await media_source.async_browse_media(self.hass, media_content_id)

        if item is not None and item.children is not None:
            old_count = len(item.children)
            item.children = [
                child for child in item.children if _media_source_content_filter(child)
            ]
            item.not_shown += old_count - len(item.children)

        return item
