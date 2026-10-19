"""The Tridonic DALI integration."""

from dali.driver.serial import DriverLubaRs232
from dali.exceptions import TransmissionError

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_DEVICE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.device_registry import AnyDeviceEntry
from homeassistant.helpers.storage import Store

from .const import CONF_BUS_POWER, DOMAIN, GEAR_ID_INFIX, unaddressed_gear_issue_id
from .coordinator import (
    STORAGE_VERSION,
    DaliCoordinator,
    clear_bus_voltage_issues,
    raise_bus_voltage_issue,
)

PLATFORMS: list[Platform] = [
    Platform.BUTTON,
    Platform.LIGHT,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
]


type DaliConfigEntry = ConfigEntry[DaliCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: DaliConfigEntry) -> bool:
    """Set up Tridonic DALI from a config entry."""
    driver = DriverLubaRs232(
        uri=f"luba232:{entry.data[CONF_DEVICE]}",
        bus_power=entry.data.get(CONF_BUS_POWER, False),
    )

    def _close_transport() -> None:
        """Close the interface's serial transport if it was opened."""
        if transport := getattr(driver, "_transport", None):
            transport.close()

    # Registered before connecting so a failed or cancelled connect, first
    # refresh, or platform setup still closes the serial transport.
    entry.async_on_unload(_close_transport)
    try:
        # Only control gear is used, so skip the control-device instance scan
        # that would broadcast quiescent mode and interrupt DALI-2 input devices.
        await driver.connect(scan_dev_inst=False)
    except TransmissionError as err:
        if transport := getattr(driver, "_transport", None):
            transport.close()
        raise_bus_voltage_issue(hass, entry, err)
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
        ) from err
    except (OSError, TimeoutError) as err:
        if transport := getattr(driver, "_transport", None):
            transport.close()
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
        ) from err
    clear_bus_voltage_issues(hass, entry.entry_id)
    coordinator = DaliCoordinator(hass, driver, entry)
    entry.async_on_unload(coordinator.close)
    await coordinator.async_load_metadata_cache()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # The initial refresh skips the slow memory-bank reads to keep setup fast;
    # read the deferred identification and luminaire data in the background.
    entry.async_create_background_task(
        hass, coordinator.async_enrich_deferred_data(), "dali_metadata_enrich"
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: DaliConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: DaliConfigEntry) -> None:
    """Remove repair issues and cached metadata of a removed config entry."""
    ir.async_delete_issue(hass, DOMAIN, unaddressed_gear_issue_id(entry.entry_id))
    clear_bus_voltage_issues(hass, entry.entry_id)
    await Store(
        hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}_metadata"
    ).async_remove()


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: DaliConfigEntry, device_entry: AnyDeviceEntry
) -> bool:
    """Allow removing a device only when its address no longer responds."""
    # A failed setup (e.g. the interface unplugged on restart) leaves the entry
    # without runtime data, yet the device-removal API still calls this; allow
    # removal then, since there is no live bus state to check against.
    if not hasattr(entry, "runtime_data"):
        return True
    coordinator = entry.runtime_data
    gear_addresses = set(coordinator.data)
    gear_prefix = f"{entry.entry_id}_{GEAR_ID_INFIX}_"
    for domain, identifier in device_entry.identifiers:
        if domain != DOMAIN:
            continue
        address = identifier.removeprefix(gear_prefix)
        if address.isdigit() and int(address) in gear_addresses:
            return False
    return True
