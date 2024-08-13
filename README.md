# Mammotion - Home Assistant Integration [![Discord](https://img.shields.io/discord/1247286396297678879)](https://discord.gg/vpZdWhJX8x)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=mikey0000&repository=mammotion-HA&category=Integration)

💬 [Join us on Discord](https://discord.gg/vpZdWhJX8x)

This integration allows you to control and monitor your Mammotion Luba, Luba 2 & Yuka robot mowers using Home Assistant.

⚠️ **Please note:** This integration is still a work in progress. You may encounter unfinished features or bugs. If you come across any issues, please open an issue on the GitHub repository. 🐛

## Roadmap 🗺️

- [x] Bluetooth (BLE) support
- [ ] Wi-Fi support
- [ ] Scheduling
- [ ] Mapping and zone management
- [ ] Firmware updates
- [ ] Automations
- [ ] More...

## Features ✨

- Start and stop the mower
- Monitor the mower's status (e.g., mowing, charging, idle)
- View the mower's battery level
- More features being added all the time!

## Prerequisites 📋

- Home Assistant installed and running
- Mower connected to your home network
- [Bluetooth proxy for Home Assistant](https://esphome.io/components/bluetooth_proxy.html)

Here's a cleaned-up version of the installation instructions for your GitHub repo README:

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

Once the integration is set up, you can control and monitor your Mammotion mower using Home Assistant. 🎉

## Troubleshooting 🔧

If you encounter any issues with the Mammotion integration, please check the Home Assistant logs for error messages. You can also try the following troubleshooting steps:

- Verify that you have Bluetooth proxy setup with Home Assistant.
- Ensure that your mower is connected to your home network and accessible from Home Assistant.
- Restart Home Assistant and check if the issue persists.

## PyMammotion Library 📚

This integration uses the [PyMammotion library](https://github.com/mikey0000/PyMammotion) to communicate with Mammotion mowers. PyMammotion provides a Python API for controlling and monitoring Mammotion robot mowers via MQTT, Cloud, and Bluetooth.

If the problem continues, please file an issue on the GitHub repository for further assistance. 🙏

## Credits 👥

[![Contributors](https://contrib.rocks/image?repo=mikey0000/Mammotion-HA)](https://github.com/mikey0000/Mammotion-HA/graphs/contributors)
