"""Config flow for Harvia Sauna integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME

from .api import HarviaApiClient, HarviaAuthError, HarviaConnectionError
from .const import (
    CONF_HEATER_MODEL,
    CONF_HEATER_POWER,
    DOMAIN,
    HEATER_MODELS,
    HEATER_POWER_OPTIONS,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)

STEP_HEATER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HEATER_MODEL, default="other"): vol.In(HEATER_MODELS),
        vol.Required(CONF_HEATER_POWER, default="10.8"): vol.In(HEATER_POWER_OPTIONS),
    }
)


class HarviaSaunaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Harvia Sauna."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._user_input: dict[str, Any] = {}
        self._user_data: dict[str, Any] | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1: MyHarvia credentials."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                api = HarviaApiClient(
                    self.hass,
                    user_input[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                )
                await api.async_authenticate()
                self._user_data = await api.async_get_user_data()

                # Save credentials for next step
                self._user_input = user_input

                # Proceed to heater setup
                return await self.async_step_heater()

            except HarviaAuthError:
                errors["base"] = "invalid_auth"
            except HarviaConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception during config flow")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_heater(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2: Heater model and power selection."""
        if user_input is not None:
            # Merge both steps' data
            full_data = {**self._user_input, **user_input}

            # Use email as unique ID to prevent duplicate entries
            await self.async_set_unique_id(self._user_data["email"])
            self._abort_if_unique_id_configured()

            # Build a nice title
            model_name = HEATER_MODELS.get(
                user_input[CONF_HEATER_MODEL], "Harvia Sauna"
            )
            power = user_input[CONF_HEATER_POWER]
            title = f"{model_name} {power} kW"

            return self.async_create_entry(
                title=title,
                data=full_data,
            )

        return self.async_show_form(
            step_id="heater",
            data_schema=STEP_HEATER_DATA_SCHEMA,
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Handle re-authentication."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle re-authentication confirmation."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                api = HarviaApiClient(
                    self.hass,
                    user_input[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                )
                await api.async_authenticate()

                entry = self.hass.config_entries.async_get_entry(
                    self.context["entry_id"]
                )
                if entry:
                    # Keep heater model/power from existing config
                    updated_data = {**entry.data, **user_input}
                    self.hass.config_entries.async_update_entry(
                        entry, data=updated_data
                    )
                    await self.hass.config_entries.async_reload(entry.entry_id)
                    return self.async_abort(reason="reauth_successful")

            except HarviaAuthError:
                errors["base"] = "invalid_auth"
            except HarviaConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception during reauth")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )
