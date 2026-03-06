"""Config flow for Harvia Sauna integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME

from .api_base import HarviaApiClientBase
from .api_factory import create_api_client, get_provider_from_entry_data
from .const import (
    API_PROVIDER_MYHARVIA,
    API_PROVIDERS,
    CONF_API_PROVIDER,
    CONF_HEATER_MODEL,
    CONF_HEATER_POWER,
    DOMAIN,
    HEATER_MODELS,
    HEATER_POWER_OPTIONS,
)
from .errors import HarviaAuthError, HarviaConnectionError

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_API_PROVIDER, default=API_PROVIDER_MYHARVIA): vol.In(
            API_PROVIDERS
        ),
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

STEP_REAUTH_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)

RECONFIGURE_PASSWORD_PLACEHOLDER = "********"


class HarviaSaunaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Harvia Sauna."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._user_input: dict[str, Any] = {}
        self._user_data: dict[str, Any] | None = None
        self._detected_model: str = "other"
        self._detected_power: str = "10.8"

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1: API provider and credentials."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                api = create_api_client(
                    self.hass,
                    user_input[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                    user_input[CONF_API_PROVIDER],
                )
                await api.async_authenticate()
                self._user_data = await api.async_get_user_data()

                # Try to auto-detect heater model from device data
                await self._async_detect_heater(api)

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

    async def _async_detect_heater(self, api: HarviaApiClientBase) -> None:
        """Try to detect heater model from device display name."""
        try:
            devices = await api.async_get_devices()
            if not devices:
                return

            device_id = devices[0]["device_id"]
            state = await api.async_get_device_state(device_id)
            display_name = state.get("displayName", "").lower()

            # Match known model names
            for key in HEATER_MODELS:
                if key != "other" and key.replace("_", " ") in display_name:
                    self._detected_model = key
                    _LOGGER.debug("Auto-detected heater model: %s", key)
                    break
        except Exception:
            _LOGGER.debug("Could not auto-detect heater model")

    async def async_step_heater(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2: Heater model and power selection."""
        if user_input is not None:
            # Merge both steps' data
            full_data = {**self._user_input, **user_input}

            # Use email as unique ID to prevent duplicate entries
            unique_email = self._user_data.get("email") if self._user_data else None
            unique_value = unique_email or self._user_input[CONF_USERNAME]
            provider = self._user_input.get(CONF_API_PROVIDER, API_PROVIDER_MYHARVIA)
            # Keep old unique-id shape for legacy provider, scope new provider IDs.
            if provider != API_PROVIDER_MYHARVIA:
                unique_value = f"{provider}:{unique_value}"
            await self.async_set_unique_id(unique_value)
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

        # Pre-fill with auto-detected values
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_HEATER_MODEL, default=self._detected_model
                ): vol.In(HEATER_MODELS),
                vol.Required(
                    CONF_HEATER_POWER, default=self._detected_power
                ): vol.In(HEATER_POWER_OPTIONS),
            }
        )

        return self.async_show_form(
            step_id="heater",
            data_schema=schema,
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Handle re-authentication."""
        return await self.async_step_reauth_confirm()

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reconfiguration of credentials, heater model and power."""
        entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        if not entry:
            return self.async_abort(reason="unknown")

        errors: dict[str, str] = {}

        if user_input is not None:
            current_username = entry.data.get(CONF_USERNAME, "")
            current_password = entry.data.get(CONF_PASSWORD, "")
            provider = get_provider_from_entry_data(entry.data)

            new_username = user_input.get(CONF_USERNAME, current_username).strip()
            submitted_password = user_input.get(CONF_PASSWORD, "")
            password_changed = (
                bool(submitted_password)
                and submitted_password != RECONFIGURE_PASSWORD_PLACEHOLDER
            )
            username_changed = new_username != current_username

            if username_changed and not password_changed:
                errors["base"] = "password_required"
            else:
                candidate_password = (
                    submitted_password if password_changed else current_password
                )

                # Validate credentials if user updated username or password.
                if username_changed or password_changed:
                    try:
                        api = create_api_client(
                            self.hass,
                            new_username,
                            candidate_password,
                            provider,
                        )
                        await api.async_authenticate()
                    except HarviaAuthError:
                        errors["base"] = "invalid_auth"
                    except HarviaConnectionError:
                        errors["base"] = "cannot_connect"
                    except Exception:
                        _LOGGER.exception("Unexpected exception during reconfigure auth")
                        errors["base"] = "unknown"

                if not errors:
                    updated_data = {
                        **entry.data,
                        CONF_HEATER_MODEL: user_input[CONF_HEATER_MODEL],
                        CONF_HEATER_POWER: user_input[CONF_HEATER_POWER],
                        CONF_USERNAME: new_username,
                    }
                    # Keep existing password unless user explicitly set a new one.
                    if password_changed:
                        updated_data[CONF_PASSWORD] = submitted_password

                    self.hass.config_entries.async_update_entry(
                        entry, data=updated_data
                    )
                    # Reload is handled by _async_update_listener
                    return self.async_abort(reason="reconfigure_successful")

        # Pre-fill with current values
        current_model = entry.data.get(CONF_HEATER_MODEL, "other")
        current_power = entry.data.get(CONF_HEATER_POWER, "10.8")
        current_username = entry.data.get(CONF_USERNAME, "")

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_USERNAME, default=current_username
                ): str,
                # Never prefill the real password in UI. User enters this only when changing it.
                vol.Optional(CONF_PASSWORD): str,
                vol.Required(
                    CONF_HEATER_MODEL, default=current_model
                ): vol.In(HEATER_MODELS),
                vol.Required(
                    CONF_HEATER_POWER, default=current_power
                ): vol.In(HEATER_POWER_OPTIONS),
            }
        )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle re-authentication confirmation."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                entry = self.hass.config_entries.async_get_entry(
                    self.context["entry_id"]
                )
                provider = get_provider_from_entry_data(entry.data) if entry else None
                api = create_api_client(
                    self.hass,
                    user_input[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                    provider,
                )
                await api.async_authenticate()

                if entry:
                    # Keep provider and heater model/power from existing config
                    updated_data = {
                        **entry.data,
                        CONF_USERNAME: user_input[CONF_USERNAME],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                    }
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
            data_schema=STEP_REAUTH_DATA_SCHEMA,
            errors=errors,
        )
