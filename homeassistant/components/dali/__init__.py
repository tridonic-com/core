"""The Tridonic DALI integration."""

from dali.driver.serial import DriverLubaRs232

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_DEVICE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .coordinator import DaliCoordinator

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.LIGHT,
    Platform.SENSOR,
]


type DaliConfigEntry = ConfigEntry[DaliCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: DaliConfigEntry) -> bool:
    """Set up Tridonic DALI from a config entry."""
    driver = DriverLubaRs232(uri=f"luba232:{entry.data[CONF_DEVICE]}")
    try:
        await driver.connect(scan_dev_inst=True)
    except (OSError, TimeoutError) as err:
        if transport := getattr(driver, "_transport", None):
            transport.close()
        raise ConfigEntryNotReady("Unable to connect to the DALI interface") from err
    coordinator = DaliCoordinator(hass, driver, entry)
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        coordinator.close()
        raise
    entry.runtime_data = coordinator
    coordinator.start_event_listener()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: DaliConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        entry.runtime_data.close()
    return unloaded
