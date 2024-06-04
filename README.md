# Mammotion - Home Assistant Integration [![Discord](https://img.shields.io/discord/1247286396297678879)](https://discord.gg/vpZdWhJX8x)

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

## Installation 🛠️

This integration can be installed using [HACS](https://hacs.xyz/)

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/integration)

1. Install the integration via HACS as mentioned above
2. Restart HA
3. Open the Home Assistant web interface.
4. Navigate to "Configuration" > "Integrations".
5. Click on the "+" button in the bottom right corner to add a new integration.
6. Search for "Mammotion" and select it from the list.
7. Select your robot mower by name when prompted.
8. Click on "Submit" to complete the integration setup.

## Usage 🎮

Once the integration is set up, you can control and monitor your Mammotion mower using Home Assistant. 🎉

## Troubleshooting 🔧

If you encounter any issues with the Mammotion integration, please check the Home Assistant logs for error messages. You can also try the following troubleshooting steps:

- Verify that you have Bluetooth proxy setup with Home Assistant.
- Ensure that your mower is connected to your home network and accessible from Home Assistant.
- Restart Home Assistant and check if the issue persists.

If the problem continues, please file an issue on the GitHub repository for further assistance. 🙏

## Credits 👥

[![Contributors](https://contrib.rocks/image?repo=mikey0000/HA-Luba)](https://github.com/mikey0000/HA-Luba/graphs/contributors)
