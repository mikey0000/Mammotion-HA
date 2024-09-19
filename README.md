# Mammotion - Home Assistant Integration [![Discord](https://img.shields.io/discord/1247286396297678879)](https://discord.gg/vpZdWhJX8x)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=mikey0000&repository=mammotion-HA&category=Integration)

💬 [Join us on Discord](https://discord.gg/vpZdWhJX8x)

This integration allows you to control and monitor your Mammotion Luba, Luba 2 & Yuka robot mowers using Home Assistant.

⚠️ **Please note:** This integration is still a work in progress. You may encounter unfinished features or bugs. If you come across any issues, please open an issue on the GitHub repository. 🐛

## Roadmap 🗺️

- [x] Bluetooth (BLE) support
- [x] Wi-Fi support (Including SIM 3G)
- [x] Scheduling
- [x] Mapping and zone management
- [x] Firmware updates
- [x] Automations
- [ ] More...

## Features ✨

- Start and stop the mower
- Monitor the mower's status (e.g., mowing, charging, idle)
- View the mower's battery level
- Start a mow based on configuration
- More features being added all the time!

## Prerequisites 📋

- Home Assistant installed and running
- Mower connected to your home network
- (Optional)[Bluetooth proxy for Home Assistant](https://esphome.io/components/bluetooth_proxy.html)
- Second account with your mower shared to it for using Wi-Fi (If you use your primary it will log you out of your mobile app)

## Installation 🛠️

This integration can be installed using [HACS](https://hacs.xyz/)

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/integration)

1. Open the Home Assistant web interface.
2. Navigate to "Configuration" > "Integrations".
3. Click the 3 dots in the top right corner and select "Custom repositories".
4. In the "Add custom repository" dialog:
   - Repository: `https://github.com/mikey0000/Mammotion-HA`
   - Category: Select "Integration"
   - Click "ADD"
5. Close the custom repositories dialog.
6. Click the "+" button in the bottom right corner to add a new integration.
7. Search for "Mammotion" and select it from the list.
8. Follow the prompts to complete the setup:
   - Select your robot mower by name when prompted.
   - Click "Submit" to finalize the integration setup.
9. Restart Home Assistant to apply the changes.

Note: If you encounter any issues, please ensure that HACS is properly installed and configured in your Home Assistant instance.

## Usage 🎮

### Getting Started
See the wiki for how to [get started](https://github.com/mikey0000/Mammotion-HA/wiki/Getting-Started)

Once the integration is set up, you can control and monitor your Mammotion mower using Home Assistant. 🎉

### Scheduling
To use the new scheduling feature, you can create schedules for your mower to start and stop mowing at specific times. This can be done through the Home Assistant interface or by using automations.

### Mapping and Zone Management
The mapping and zone management feature allows you to create, update, and delete zones for your mower. You can also retrieve and display zone information through the Home Assistant interface.

### Firmware Updates
The firmware update feature allows you to check for, download, and install firmware updates for your mower. This can be done through the Home Assistant interface.

### Automations
The automation feature allows you to create, update, and delete automations for your mower. You can trigger automations based on the mower's status and events.

## Troubleshooting 🔧

If you encounter any issues with the Mammotion integration, please check the Home Assistant logs for error messages. You can also try the following troubleshooting steps:

- Verify that you have Bluetooth proxy setup with Home Assistant.
- Ensure that your mower is connected to your home network and accessible from Home Assistant.
- Restart Home Assistant and check if the issue persists.

## PyMammotion Library 📚

This integration uses the [PyMammotion library](https://github.com/mikey0000/PyMammotion) to communicate with Mammotion mowers. PyMammotion provides a Python API for controlling and monitoring Mammotion robot mowers via MQTT, Cloud, and Bluetooth.

If the problem continues, please file an issue on the GitHub repository for further assistance. 🙏

## Support me
<a href='https://ko-fi.com/DenimJackRabbit' target='_blank'><img height='46' style='border:0px;height:46px;' src='https://az743702.vo.msecnd.net/cdn/kofi3.png?v=0' border='0' alt='Buy Me a Coffee at ko-fi.com' /></a>

[Buy a Mammotion Lawn mower](https://mammotion.com/?ref=tbbzqsog)

## Credits 👥

[![Contributors](https://contrib.rocks/image?repo=mikey0000/Mammotion-HA)](https://github.com/mikey0000/Mammotion-HA/graphs/contributors)
