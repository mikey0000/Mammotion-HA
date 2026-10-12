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
  - [ESP32-S3 mini (Amazon)](https://www.amazon.com/s?k=ESP32-S3+mini&tag=homeassistant-mammotion-20)
  - [USB-A to USB-C 0.3 m data cable (Amazon)](https://www.amazon.com/s?k=USB+A+to+USB+C+0.3m+data+cable&tag=homeassistant-mammotion-20)

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

## Camera Re-streaming (Frigate, go2rtc)

The camera entities stream over WebRTC through Agora, so Home Assistant has no RTSP URL to hand an NVR. The integration can instead register each camera with a go2rtc server, such as the one inside Frigate, which then serves it as RTSP.

1. Make go2rtc's API reachable from Home Assistant. For Frigate that means publishing port `1984` of the container; keep it on your LAN, since go2rtc's API has no authentication unless you configure it (`http://user:password@host:1984` is supported).
2. In **Settings > Devices & Services > Mammotion > Configure**, set **go2rtc server** to the API address, for example `http://frigate:1984`.
3. Check that go2rtc can reach Home Assistant's internal URL (**Settings > System > Network**), by a name or address the go2rtc container resolves and, for `https`, with a certificate it trusts. go2rtc pulls each camera from Home Assistant, not the other way round.

Each camera then appears in go2rtc as `mammotion_<device>_<camera>`, for example `mammotion_luba_mnab12cd_camera` and `mammotion_luba_mnab12cd_camera_right`. The streams are added to go2rtc's memory, not its config file, and are re-registered every 30 seconds, so they come back after either side restarts.

A Frigate camera using it:

```yaml
cameras:
  luba_front:
    ffmpeg:
      inputs:
        - path: rtsp://127.0.0.1:8554/mammotion_luba_mnab12cd_camera
          input_args: preset-rtsp-restream
          roles: [record, detect]
```

Things to know before relying on it:

- **The mowers publish H.265.** Frigate records it as is; detection decodes it, which costs more CPU than H.264. To transcode, add a go2rtc stream such as `luba_front_h264: ffmpeg:rtsp://127.0.0.1:8554/mammotion_luba_mnab12cd_camera#video=h264` and point Frigate at that instead.
- **Every view goes through Mammotion's cloud (Agora)**, also when go2rtc pulls it. A 24/7 Frigate recording keeps the mower's camera streaming all the time; on 4G it uses mobile data and Mammotion's streaming budget for the device.
- **One viewer per camera.** Opening the camera in a Home Assistant dashboard takes the stream from go2rtc, and go2rtc takes it back when it reconnects. Watch it through Frigate or go2rtc instead.
- go2rtc never says when it stops pulling, so the integration closes a camera's stream within a minute of go2rtc having no consumer for it.
- When a camera fails to start (mower asleep, offline or out of streaming budget), go2rtc is refused for 30 seconds, then longer on each further failure up to 10 minutes, so Frigate's constant retries do not keep waking the mower or asking Mammotion's cloud for stream tokens.
- Leaving the field empty turns re-streaming off; nothing is sent to go2rtc then.

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
