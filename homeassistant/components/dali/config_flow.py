"""Config flow for the Tridonic DALI integration."""

from typing import Any, override

from dali.driver.serial import DriverLubaRs232
import probatio

from homeassistant.components import usb
from homeassistant.components.usb import (
    SerialDevice,
    USBDevice,
    async_scan_serial_ports,
    human_readable_device_name,
    usb_service_info_from_device,
    usb_unique_id_from_service_info,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_DEVICE, CONF_NAME
from homeassistant.helpers.service_info.usb import UsbServiceInfo

from .const import DOMAIN


def _serial_port_label(port: SerialDevice) -> str:
    """Return a human readable label for a scanned serial port."""
    if isinstance(port, USBDevice):
        return human_readable_device_name(
            port.device,
            port.serial_number,
            port.manufacturer,
            port.description,
            port.vid,
            port.pid,
        )
    return port.device


class DaliConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Tridonic DALI."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._device: str | None = None
        self._ports: dict[str, SerialDevice] = {}

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a manually initiated flow by selecting a serial port."""
        self._ports = {
            port.device: port for port in await async_scan_serial_ports(self.hass)
        }
        if not self._ports:
            return self.async_abort(reason="no_serial_ports")

        errors: dict[str, str] = {}
        if user_input is not None:
            port = self._ports[user_input[CONF_DEVICE]]
            if isinstance(port, USBDevice):
                await self.async_set_unique_id(
                    usb_unique_id_from_service_info(usb_service_info_from_device(port))
                )
                self._abort_if_unique_id_configured()
            device = await self.hass.async_add_executor_job(
                usb.get_serial_by_id, port.device
            )
            driver = DriverLubaRs232(uri=f"luba232:{device}")
            try:
                await driver.connect()
            except OSError, TimeoutError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(
                    title=_serial_port_label(port),
                    data={CONF_DEVICE: device},
                )
            finally:
                if transport := getattr(driver, "_transport", None):
                    transport.close()

        return self.async_show_form(
            step_id="user",
            data_schema=probatio.Schema(
                {
                    probatio.Required(CONF_DEVICE): probatio.In(
                        {
                            device: _serial_port_label(port)
                            for device, port in self._ports.items()
                        }
                    )
                }
            ),
            errors=errors,
        )

    @override
    async def async_step_usb(self, discovery_info: UsbServiceInfo) -> ConfigFlowResult:
        """Handle discovery of a Lunatone USB-DALI2 interface."""
        await self.async_set_unique_id(usb_unique_id_from_service_info(discovery_info))
        self._device = await self.hass.async_add_executor_job(
            usb.get_serial_by_id, discovery_info.device
        )
        self._abort_if_unique_id_configured(updates={CONF_DEVICE: self._device})
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
