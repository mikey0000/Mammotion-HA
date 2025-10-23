# Mammotion - Home Assistant Integration [![Discord](https://img.shields.io/discord/1247286396297678879)](https://discord.gg/vpZdWhJX8x)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=mikey0000&repository=mammotion-HA&category=Integration)

💬 [Join us on Discord](https://discord.gg/vpZdWhJX8x)

This integration allows you to control and monitor Mammotion products, e.g robot lawn mowers using Home Assistant.

⚠️ **Please note:** This integration is still a work in progress. You may encounter unfinished features or bugs. If you come across any issues, please open an issue on the GitHub repository. 🐛

## Roadmap 🗺️

- [x] Bluetooth (BLE) support
- [x] Wi-Fi support (Including SIM 3G)
- [ ] Scheduling
- [ ] Mapping and zone management
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

## Prerequisites 📋

> [!WARNING]
> **Home Assistant Minimum Version 2025.3.0**

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
