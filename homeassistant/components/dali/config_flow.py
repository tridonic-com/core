"""Config flow for the Tridonic DALI integration."""

from typing import Any, override

from dali.driver.serial import DriverLubaRs232
import probatio

from homeassistant.components import usb
from homeassistant.components.usb import (
    human_readable_device_name,
    usb_service_info_from_device,
    usb_unique_id_from_service_info,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_DEVICE, CONF_NAME
from homeassistant.helpers.service_info.usb import UsbServiceInfo

from .const import DOMAIN


def _unique_id_from_port(port: usb.USBDevice | usb.SerialDevice) -> str:
    """Build the unique ID that USB discovery would assign to this port."""
    if isinstance(port, usb.USBDevice):
        return usb_unique_id_from_service_info(usb_service_info_from_device(port))
    return port.device


class DaliConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Tridonic DALI."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._device: str | None = None

    async def _async_can_connect(self, device: str) -> bool:
        """Check whether a DALI interface can be opened on this port."""
        driver = DriverLubaRs232(uri=f"luba232:{device}")
        try:
            await driver.connect()
        except OSError, TimeoutError:
            return False
        finally:
            if transport := getattr(driver, "_transport", None):
                transport.close()
        return True

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a flow initiated by the user."""
        configured = {entry.unique_id for entry in self._async_current_entries()}
        port_map = {
            human_readable_device_name(
                port.device,
                port.serial_number,
                port.manufacturer,
                port.description,
                port.vid if isinstance(port, usb.USBDevice) else None,
                port.pid if isinstance(port, usb.USBDevice) else None,
            ): port
            for port in await usb.async_scan_serial_ports(self.hass)
            if _unique_id_from_port(port) not in configured
        }
        if not port_map:
            return self.async_abort(reason="no_devices_found")

        errors: dict[str, str] = {}
        if user_input is not None:
            port = port_map[user_input[CONF_DEVICE]]
            await self.async_set_unique_id(_unique_id_from_port(port))
            self._abort_if_unique_id_configured()
            device = await self.hass.async_add_executor_job(
                usb.get_serial_by_id, port.device
            )
            if await self._async_can_connect(device):
                return self.async_create_entry(
                    title=user_input[CONF_DEVICE], data={CONF_DEVICE: device}
                )
            errors[CONF_DEVICE] = "cannot_connect"

        return self.async_show_form(
            step_id="user",
            data_schema=probatio.Schema(
                {probatio.Required(CONF_DEVICE): probatio.In(list(port_map))}
            ),
            errors=errors,
        )

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
        if not await self._async_can_connect(self._device):
            return self.async_abort(reason="cannot_connect")
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
