# SPDX-FileCopyrightText: © 2024 Jon Lech Johansen <jon@nanocrew.net>
# SPDX-License-Identifier: Apache-2.0

"""Config flow for Dahua VCS-SH30 PoE speaker integration."""

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_USERNAME, CONF_PASSWORD
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers import selector

from .const import DEFAULT_NAME, DEFAULT_USERNAME, DOMAIN
from .dahua_client import DahuaClient, AuthError, ConnectError

DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): selector.TextSelector(),
        vol.Required(CONF_USERNAME, default=DEFAULT_USERNAME): selector.TextSelector(),
        vol.Required(CONF_PASSWORD): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
        ),
    }
)


class DahuaSpeakerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Dahua Speaker."""

    VERSION = 1
    _config: dict[str, Any] = {}
    _reauth_entry: ConfigEntry | None = None

    async def async_step_user(
        self, user_input: dict[str, str] | None = None
    ) -> ConfigFlowResult:
        """Handle a flow initialized by the user."""
        errors = {}
        if user_input is not None:
            client = DahuaClient(
                self.hass,
                host=user_input[CONF_HOST],
                username=user_input[CONF_USERNAME],
                password=user_input[CONF_PASSWORD],
            )
            try:
                await client.login()
                device_info = await client.get_device_info()
            except ConnectError:
                errors["base"] = "cannot_connect"
            except AuthError:
                errors["base"] = "invalid_auth"
            except Exception:
                errors["base"] = "unknown"
            else:
                if reauth_entry := self._reauth_entry:
                    data = {**reauth_entry.data, **user_input}
                    self.hass.config_entries.async_update_entry(reauth_entry, data=data)
                    self.hass.async_create_task(
                        self.hass.config_entries.async_reload(reauth_entry.entry_id)
                    )
                    return self.async_abort(reason="reauth_successful")

                mac_addr = format_mac(device_info["mac"])
                await self.async_set_unique_id(mac_addr)
                self._abort_if_unique_id_configured()
                self._async_abort_entries_match({CONF_HOST: user_input[CONF_HOST]})
                self._config.update(user_input)
                return self.async_create_entry(title=DEFAULT_NAME, data=self._config)

        suggested_values: Mapping[str, Any] | None = user_input
        if self._reauth_entry and not suggested_values:
            suggested_values = self._reauth_entry.data

        data_schema = self.add_suggested_values_to_schema(DATA_SCHEMA, suggested_values)
        return self.async_show_form(
            step_id="user", data_schema=data_schema, errors=errors
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle failed credentials."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        self.context["title_placeholders"] = {
            "host": self._reauth_entry.data[CONF_HOST]
        }
        return await self.async_step_user()
