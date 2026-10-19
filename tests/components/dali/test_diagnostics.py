"""Test the Tridonic DALI diagnostics."""

from unittest.mock import patch

from syrupy.assertion import SnapshotAssertion

from homeassistant.components.dali.const import DOMAIN
from homeassistant.const import CONF_DEVICE
from homeassistant.core import HomeAssistant

from .conftest import FakeDaliDriver

from tests.common import MockConfigEntry
from tests.components.diagnostics import get_diagnostics_for_config_entry
from tests.typing import ClientSessionGenerator


async def test_entry_diagnostics(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    snapshot: SnapshotAssertion,
    fake_dali_driver: FakeDaliDriver,
) -> None:
    """Test config entry diagnostics dump gears and discovered instances."""
    fake_dali_driver.dev_inst_map.mapping = {(1, 2): 1, (1, 3): 3}
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_DEVICE: "/dev/ttyACM0"}, title="USB-DALI2"
    )
    entry.add_to_hass(hass)

    with patch(
        "homeassistant.components.dali.DriverLubaRs232",
        return_value=fake_dali_driver,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert await get_diagnostics_for_config_entry(hass, hass_client, entry) == snapshot

    assert await hass.config_entries.async_unload(entry.entry_id)
