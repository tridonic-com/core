"""Config flow for the Tridonic DALI integration."""

from typing import Any, override

from dali.driver.serial import DriverLubaRs232

from homeassistant.components import usb
from homeassistant.components.usb import (
    human_readable_device_name,
    usb_unique_id_from_service_info,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_DEVICE, CONF_NAME
from homeassistant.helpers.service_info.usb import UsbServiceInfo

from .const import DOMAIN


class DaliConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Tridonic DALI."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._device: str | None = None

    @override
    async def async_step_usb(self, discovery_info: UsbServiceInfo) -> ConfigFlowResult:
        """Handle discovery of a Lunatone USB-DALI2 interface."""
        await self.async_set_unique_id(usb_unique_id_from_service_info(discovery_info))
        self._abort_if_unique_id_configured(
            updates={CONF_DEVICE: discovery_info.device}
        )
        self.context["title_placeholders"] = {
            CONF_NAME: human_readable_device_name(
                discovery_info.device,
                discovery_info.serial_number,
                discovery_info.manufacturer,
                discovery_info.description,
                discovery_info.vid,
                discovery_info.pid,
            )
        }
        self._device = await self.hass.async_add_executor_job(
            usb.get_serial_by_id, discovery_info.device
        )
        driver = DriverLubaRs232(uri=f"luba232:{self._device}")
        try:
            await driver.connect()
        except OSError, TimeoutError:
            return self.async_abort(reason="cannot_connect")
        finally:
            if transport := getattr(driver, "_transport", None):
                transport.close()
        return await self.async_step_usb_confirm()

    async def async_step_usb_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm the discovered USB-DALI2 interface."""
        if user_input is not None:
            assert self._device is not None
            return self.async_create_entry(
                title=self.context["title_placeholders"][CONF_NAME],
                data={CONF_DEVICE: self._device},
            )
        self._set_confirm_only()
        return self.async_show_form(step_id="usb_confirm")
