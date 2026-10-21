# Tridonic DALI

The Tridonic DALI integration connects Home Assistant to Lunatone USB-DALI2 interfaces and the DALI control gears attached to them.

## Installation

1. Connect a Lunatone USB-DALI2 interface to the Home Assistant host.
2. Connect the DALI control gear to the interface and assign each ballast a unique DALI short address.
3. In Home Assistant, open **Settings > Devices & services**.
4. Select **Add integration** and choose **Tridonic DALI**.
5. Confirm the discovered USB-DALI2 interface.

The interface must be available as a serial device. Home Assistant uses the stable serial device path when configuring the integration.

## Devices and updates

The integration scans the DALI short-address range when it starts and every 30 seconds afterward. Each responding control gear is added as a light. Gear that stops responding becomes unavailable, and newly responding gear is added automatically.

The integration reads optional identification data from DALI memory bank 0 when available, including the GTIN, identification number, firmware version, and hardware version.

## Control

Lights support on, off, and brightness control, with colour temperature and CIE xy colour on colour control (DT8) gear.

Control gear is added from its presence on the bus rather than from the IEC 62386 part 2xx device type it reports, so fluorescent (DT0), HID (DT2), halogen (DT3), incandescent (DT4), DC-controlled (DT5), LED (DT6) and colour (DT8) gear are all exposed, as is gear reporting no device type at all. Switching function (DT7) gear is exposed as an on/off light. A self-contained emergency (DT1) gear only becomes a light when it is a maintained type, since a non-maintained one has no normal-mode output.

## Removal

1. Open **Settings > Devices & services**.
2. Select **Tridonic DALI**.
3. Open the configured USB-DALI2 interface and select the menu.
4. Select **Delete**.
5. Disconnect the USB-DALI2 interface if it is no longer needed.

No device-side removal step is required.
