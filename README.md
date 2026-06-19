# Mammotion - Home Assistant Integration [![Discord](https://img.shields.io/discord/1247286396297678879)](https://discord.gg/vpZdWhJX8x)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=mikey0000&repository=mammotion-HA&category=Integration)

💬 [Join us on Discord](https://discord.gg/vpZdWhJX8x)

This integration allows you to control and monitor Mammotion products, e.g robot lawn mowers using Home Assistant.

⚠️ **Please note:** This integration is still a work in progress. You may encounter unfinished features or bugs. If you come across any issues, please open an issue on the GitHub repository. 🐛

## Roadmap 🗺️

- [x] Bluetooth (BLE) support
- [x] Wi-Fi support (Including SIM 3G/4G)
- [x] Camera stream
- [x] Scheduling
- [ ] Mapping and zone management
- [x] Maps
- [x] Firmware updates
- [x] Automations
- [ ] More...

## Features ✨

- Start, stop, pause, and dock the mower
- Monitor the mower's status (e.g., mowing, charging, idle)
- View the mower's battery level
- Start a mow based on configuration
- Start an existing scheduled task/s
- More features being added all the time!

- Supports Spino pool cleaners

## Prerequisites 📋

> [!WARNING]
> **Home Assistant Minimum Version 2026.1.0**

- A second account with your mower/s shared to it for using Wi-Fi (If you use your primary accouunt it will log you out of your mobile app)
- (Optional)[Bluetooth proxy for Home Assistant](https://esphome.io/components/bluetooth_proxy.html)

## Troubleshooting

- Sometimes using the account number works instead of email address when adding via discovery (not sure why)

- Connection timeout to host https://api.link.aliyun.com/living/account/region/get - unblock china

## Installation 🛠️

This integration can be installed using [HACS](https://hacs.xyz/)

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/integration)

This integration is not available in the default HACS store. You will need to add it as a custom repository.

1. Go to HACS > Integrations and click on the 3 dots in the top right corner.
2. Select "Custom repositories".
3. In the "Repository" field, paste this URL: `https://github.com/mikey0000/Mammotion-HA`
4. For "Category", select "Integration".
5. Click "Add".
6. You can now search for "Mammotion" within HACS and install it.
7. After installation, restart Home Assistant.
8. Go to **Settings > Devices & Services** and click **+ Add Integration** to configure Mammotion.

## Usage 🎮

### Getting Started

See the wiki for how to [get started](https://github.com/mikey0000/Mammotion-HA/wiki/Getting-Started)

Once the integration is set up, you can control and monitor your Mammotion mower using Home Assistant. 🎉

## Map Position Offset

Satellite map tiles (Google Maps, OpenStreetMap, etc.) are sometimes misaligned relative to RTK GPS coordinates by several metres. Each mower exposes two number entities to correct this:

- **Map offset latitude** — shifts the mower pin north (positive) or south (negative), in metres
- **Map offset longitude** — shifts the mower pin east (positive) or west (negative), in metres

**How to calibrate:**

1. Add a [Map card](https://www.home-assistant.io/dashboards/map/) and both offset entities to a Lovelace dashboard.
2. Start the mower so it is moving at a known location you can identify on satellite imagery.
3. Adjust **Map offset latitude** and **Map offset longitude** until the pin aligns with the mower's real position on the satellite layer.
4. Values are saved automatically and survive restarts.

Typical offsets are within ±20 m. Positive latitude = north, positive longitude = east.

## Dashboard Plugins

Companion HACS dashboard plugins that extend the Mammotion integration with visual tools.

### Mammotion Assets

Images and scripts for displaying Mammotion mowers on a map in Home Assistant — mower card backgrounds, side-profile images, map icons, RTK/dock assets, and the `geojson.js` script that renders mowing areas with labels.

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=mikey0000&repository=ha-mammotion-assets&category=plugin)

### Mammotion GeoJSON Map Plugin

A Lovelace resource that renders GeoJSON mowing areas on the map with area names and zone labels.

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=mikey0000&repository=ha-mammotion-geojson-map-plugin&category=plugin)

### Mammotion SVG Pick and Place

An interactive Lovelace card for placing, editing, and deleting SVG pattern tiles on your mower's map directly from the dashboard. Load an SVG, drag it into position, scale and rotate it, then send it to the device in one click via the `mammotion.svg_add` service.

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=mikey0000&repository=ha-mammotion-svg-pick-n-place&category=plugin)

## Scheduling

A community scheduler blueprint is available for Mammotion mowers using the newer Mammotion Home Assistant integration entity model. It is designed for Luba/Yuka-style setups where each mowing area is exposed as a Mammotion area switch, such as switch.my_mower_area_area_1, switch.my_mower_area_area_2, and switch.my_mower_area_area_3.

The scheduler supports 1–6 active zones, up to 4 daily mowing sessions, 1 or 2 zones per session, rain and wet-lawn skipping, manual “Start Next Mow” controls, low-battery resume protection, charge-and-resume handling, and safer recovery after Home Assistant restarts or automation reloads.

Only one blueprint is needed. Earlier versions used a separate completion watcher, but the current scheduler blueprint handles starting, waiting, completion detection, rain recall, timeout handling, helper reset, and mid-mow charging resume inside the main automation.

### What it does

The scheduler rotates through configured Mammotion area switches in order. Before each zone, it turns off the active area switches, waits briefly, turns on the target area switch, waits for the mower/integration to register the selected area, applies optional mower settings, and starts mowing through the lawn_mower entity.

It tracks the last completed zone with an input_number helper and uses an input_boolean helper to know when the scheduler is actively managing a run. It waits for real completion signals before advancing the zone tracker, so a timeout does not count as a completed mow.

If the scheduler-in-progress helper gets stuck on while the mower is clearly docked, charging, ready, and not working, the blueprint can clear the stale helper automatically instead of incorrectly skipping the next session.

If the mower docks to recharge mid-zone, the scheduler can detect the paused charging state and resume the same mowing task once the battery is ready.

### Required helpers

Create these helpers in Settings → Devices & services → Helpers:

* input_number.mower_last_zone
    * Type: Number
    * Min: 0
    * Max: 6
    * Step: 1
    * Initial value: 0
* input_boolean.mower_manual_skip
    * Type: Toggle
    * Turn on to temporarily skip scheduled mowing.
* input_boolean.mower_scheduler_in_progress
    * Type: Toggle
    * Used by the scheduler to prevent overlapping runs and recover from restarts/reloads.
* input_datetime.mower_last_run
    * Type: Date and time
    * Stores the last successful scheduler-managed run time.
* input_datetime.mower_rain_last_seen
    * Type: Date and time
    * Stores when rain was last detected for the wet-lawn dry-out delay.
* input_button.mower_start_next_mow
    * Type: Button
    * Optional but recommended for dashboard/manual starts.

### Required entities

You will need:

* A Mammotion lawn_mower.* entity
* Mammotion area switch.* entities for each work area
* A Home Assistant weather.* entity
* Optional Mammotion status sensors, such as battery, activity mode, progress, time left, last error, last error time, last error code, and charging state

For weather, Open-Meteo is a simple option because it provides a weather.* entity without requiring an account or API key. Add it from Settings → Devices & services → Add Integration → Open-Meteo, then select the resulting weather entity in the scheduler blueprint.

### Rain behavior

The scheduler checks weather at session start, during the wet-lawn delay check, before the second zone if mowing 2 zones in one session, and during the active mowing wait window so the mower can be recalled if rain appears mid-session.

Rain states treated as unsafe include:

rainy, pouring, lightning-rainy, hail, snowy-rainy, and exceptional.

Recommended first test

After importing the blueprint, create one automation from it and start conservatively:

* Active Zone Count: 1
* Zones Per Session: 1
* Zone Duration Ceiling: 240 minutes
* Select only one known-good Mammotion area switch
* Confirm the mower starts, completes, docks, and advances the zone tracker

Once the first zone works reliably, increase the active zone count and add more sessions.

### Blueprint

<a href="https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgist.github.com%2Fliquidbear99%2F1f7448f357524e9281565d289fe98334" target="_blank" rel="noreferrer noopener"><img src="https://my.home-assistant.io/badges/blueprint_import.svg" alt="Open your Home Assistant instance and show the blueprint import dialog with a specific blueprint pre-filled." /></a>

## Troubleshooting 🔧

If you encounter any issues with the Mammotion integration, please check the Home Assistant logs for error messages. You can also try the following troubleshooting steps:

- Verify that you have Bluetooth proxy setup with Home Assistant.
- Ensure that your mower is connected to your home network and accessible from Home Assistant.
- Restart Home Assistant and check if the issue persists.
- Make sure your not blocking China (Connection timeout to host https://api.link.aliyun.com/living/account/region/get)

## Contributing to Translations

We use Crowdin to manage our translations. If you'd like to contribute:

1. Visit our [Crowdin project page](https://crowdin.com/project/mammotion-ha)
2. Select the language you'd like to translate to
3. Start translating!

Your contributions will be automatically submitted as pull requests to this repository.

## PyMammotion Library 📚

This integration uses the [PyMammotion library](https://github.com/mikey0000/PyMammotion) to communicate with Mammotion mowers. PyMammotion provides a Python API for controlling and monitoring Mammotion robot mowers via MQTT, Cloud, and Bluetooth.

If the problem continues, please file an issue on the GitHub repository for further assistance. 🙏

## Support me

<a href='https://ko-fi.com/DenimJackRabbit' target='_blank'><img height='46' style='border:0px;height:46px;' src='https://az743702.vo.msecnd.net/cdn/kofi3.png?v=0' border='0' alt='Buy Me a Coffee at ko-fi.com' /></a>

### Referral Links

[Buy a Mammotion Lawn Mower (Amazon)](https://amzn.to/4cOLULU)
[Buy a Mammotion Lawn Mower (Mammotion)](https://mammotion.com/?ref=denimjackrabbit)

## Credits 👥

[![Contributors](https://contrib.rocks/image?repo=mikey0000/Mammotion-HA)](https://github.com/mikey0000/Mammotion-HA/graphs/contributors)
