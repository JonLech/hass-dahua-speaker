# SPDX-FileCopyrightText: © 2024 Jon Lech Johansen <jon@nanocrew.net>
# SPDX-License-Identifier: Apache-2.0

"""Dahua VCS-SH30 PoE speaker integration."""

from dataclasses import dataclass

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_USERNAME, CONF_PASSWORD, Platform
from homeassistant.exceptions import ConfigEntryNotReady, ConfigEntryAuthFailed

from .dahua_client import DahuaClient, AuthError, ConnectError

PLATFORMS = [Platform.MEDIA_PLAYER]

type DahuaSpeakerConfigEntry = ConfigEntry[DahuaSpeakerData]


@dataclass
class DahuaSpeakerData:
    """Runtime data definition."""

    client: DahuaClient
    device_info: dict


async def async_setup_entry(
    hass: HomeAssistant, entry: DahuaSpeakerConfigEntry
) -> bool:
    """Set up Dahua Speaker from a config entry."""
    config = entry.data

    host = config[CONF_HOST]
    username = config[CONF_USERNAME]
    password = config[CONF_PASSWORD]

    client = DahuaClient(hass, host=host, username=username, password=password)

    try:
        await client.login()
        device_info = await client.get_device_info()
    except ConnectError as err:
        raise ConfigEntryNotReady from err
    except AuthError as err:
        raise ConfigEntryAuthFailed from err

    entry.runtime_data = DahuaSpeakerData(client, device_info)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
