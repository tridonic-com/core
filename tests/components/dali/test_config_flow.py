"""Test the Tridonic DALI config flow."""

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.dali.const import DOMAIN
from homeassistant.components.usb import USBDevice, usb_unique_id_from_service_info
from homeassistant.config_entries import SOURCE_USB, SOURCE_USER
from homeassistant.const import CONF_DEVICE, CONF_SOURCE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.usb import UsbServiceInfo

from tests.common import MockConfigEntry

DISCOVERY_INFO = UsbServiceInfo(
    device="/dev/ttyACM0",
    vid="0403",
    pid="6015",
    serial_number="123456",
    manufacturer="Lunatone",
    description="USB-DALI2",
)

USB_PORT = USBDevice(
    device="/dev/ttyACM0",
    vid="0403",
    pid="6015",
    serial_number="123456",
    manufacturer="Lunatone",
    description="USB-DALI2",
)

PORT_LABEL = "USB-DALI2 - /dev/ttyACM0, s/n: 123456 - Lunatone - 0403:6015"
BY_ID_PATH = "/dev/serial/by-id/usb-Lunatone_USB-DALI2"


async def test_usb_discovery(hass: HomeAssistant, mock_setup_entry: AsyncMock) -> None:
    """Test USB discovery creates an entry for the interface."""
    with (
        patch(
            "homeassistant.components.dali.config_flow.usb.get_serial_by_id",
            return_value="/dev/serial/by-id/usb-Lunatone_USB-DALI2",
        ),
        patch("homeassistant.components.dali.config_flow.DriverLubaRs232") as driver,
    ):
        driver.return_value.connect = AsyncMock()
        driver.return_value._transport = MagicMock()
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={CONF_SOURCE: SOURCE_USB},
            data=DISCOVERY_INFO,
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "usb_confirm"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_DEVICE: "/dev/serial/by-id/usb-Lunatone_USB-DALI2"}
    assert result["result"].unique_id
    assert len(mock_setup_entry.mock_calls) == 1
    driver.return_value.connect.assert_awaited_once_with()
    driver.return_value._transport.close.assert_called_once_with()


async def test_usb_discovery_cannot_connect(hass: HomeAssistant) -> None:
    """Test USB discovery aborts when the interface cannot be opened."""
    with (
        patch(
            "homeassistant.components.dali.config_flow.usb.get_serial_by_id",
            return_value="/dev/serial/by-id/usb-Lunatone_USB-DALI2",
        ),
        patch("homeassistant.components.dali.config_flow.DriverLubaRs232") as driver,
    ):
        driver.return_value.connect = AsyncMock(side_effect=OSError)
        driver.return_value._transport = MagicMock()
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={CONF_SOURCE: SOURCE_USB},
            data=DISCOVERY_INFO,
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"
    driver.return_value._transport.close.assert_called_once_with()


async def test_user_flow(hass: HomeAssistant, mock_setup_entry: AsyncMock) -> None:
    """Test manually adding an interface from the serial port list."""
    with (
        patch(
            "homeassistant.components.dali.config_flow.usb.async_scan_serial_ports",
            return_value=[USB_PORT],
        ),
        patch(
            "homeassistant.components.dali.config_flow.usb.get_serial_by_id",
            return_value=BY_ID_PATH,
        ),
        patch("homeassistant.components.dali.config_flow.DriverLubaRs232") as driver,
    ):
        driver.return_value.connect = AsyncMock()
        driver.return_value._transport = MagicMock()
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={CONF_SOURCE: SOURCE_USER}
        )

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_DEVICE: PORT_LABEL}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_DEVICE: BY_ID_PATH}
    assert result["result"].unique_id == usb_unique_id_from_service_info(DISCOVERY_INFO)
    assert len(mock_setup_entry.mock_calls) == 1


async def test_user_flow_no_devices_found(hass: HomeAssistant) -> None:
    """Test the user flow aborts when no serial ports are available."""
    with patch(
        "homeassistant.components.dali.config_flow.usb.async_scan_serial_ports",
        return_value=[],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={CONF_SOURCE: SOURCE_USER}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"


async def test_user_flow_cannot_connect(
    hass: HomeAssistant, mock_setup_entry: AsyncMock
) -> None:
    """Test the user flow recovers after a failed connection."""
    with (
        patch(
            "homeassistant.components.dali.config_flow.usb.async_scan_serial_ports",
            return_value=[USB_PORT],
        ),
        patch(
            "homeassistant.components.dali.config_flow.usb.get_serial_by_id",
            return_value=BY_ID_PATH,
        ),
        patch("homeassistant.components.dali.config_flow.DriverLubaRs232") as driver,
    ):
        driver.return_value.connect = AsyncMock(side_effect=OSError)
        driver.return_value._transport = MagicMock()
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={CONF_SOURCE: SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_DEVICE: PORT_LABEL}
        )

        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {CONF_DEVICE: "cannot_connect"}

        driver.return_value.connect = AsyncMock()
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_DEVICE: PORT_LABEL}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_DEVICE: BY_ID_PATH}


async def test_user_flow_already_configured(hass: HomeAssistant) -> None:
    """Test the user flow hides interfaces that are already configured."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=usb_unique_id_from_service_info(DISCOVERY_INFO),
        data={CONF_DEVICE: BY_ID_PATH},
    )
    entry.add_to_hass(hass)

    with patch(
        "homeassistant.components.dali.config_flow.usb.async_scan_serial_ports",
        return_value=[USB_PORT],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={CONF_SOURCE: SOURCE_USER}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"


async def test_usb_discovery_already_configured(hass: HomeAssistant) -> None:
    """Test USB discovery aborts for an already configured interface."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=usb_unique_id_from_service_info(DISCOVERY_INFO),
        data={CONF_DEVICE: "/dev/serial/by-id/usb-Lunatone_USB-DALI2"},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={CONF_SOURCE: SOURCE_USB},
        data=DISCOVERY_INFO,
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
