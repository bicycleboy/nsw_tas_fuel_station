
<p align="center">
  <a href="https://github.com/custom-components/hacs"><img src="https://img.shields.io/badge/HACS-Custom-orange.svg"></a>
  <img src="https://img.shields.io/github/v/release/bicycleboy/nsw_tas_fuel_station" alt="Current version">
</p>

# NSW Fuel Check Integration

Integration allowing fuel prices to be included in Home Assistant dashboards.

## Feedback
Feedback, issues and feature requests are welcome and can be made [here](https://github.com/bicycleboy/nsw_tas_fuel_station/issues). And please star this repository. 

## Features
- Allows users to include NSW, ACT and Tasmanian fuel prices into their home assistant dashboards and automations.  Currently only these Australian states are supported as other states offer different APIs.
- This 2026 update to the existing core integration allows the user to configure the integration via the user interface (vs configuration.yaml) and adds sensors for the cheapest fuel found by the API.
- September 2026 release adds the ability to choose the fuel type searched for by the cheapest sensors primarily to support Diesel and U95/U98.  This release also adds the ability to exclude stations from the cheapest sensors such as members only brands.
- Reconfigure now provides dedicated paths to add a new location, add stations to an existing location, edit existing nickname/location settings, and manage configured stations and their fuel types. Stations can be removed individually, and removing the final station from a nickname/location can also remove the now-empty location and its entities after confirmation.

## Example Cards for Your Home Assistant Dashboard

![example cards](./images/example_cards.png)

[Example card yaml](https://github.com/bicycleboy/nsw_tas_fuel_station/blob/main/example_cards.yaml)

## Managing an existing configuration

After the integration has been set up, existing locations, stations and fuel types are managed from the integration's **Reconfigure** flow.

In Home Assistant go to:

**Settings -> Devices & services -> NSW Fuel Check -> three dots -> Reconfigure**

The Reconfigure menu provides:

- **Add new location** - create a new nickname/location and select its initial stations.
- **Add station to existing location** - choose an existing location from a dropdown, then search for and add another station without changing that location's saved cheapest-fuel settings.
- **Edit existing location settings** - change an existing location/search radius, cheapest-fuel search type, or cheapest-station exclusion text without changing its favorite stations.
- **Manage configured stations** - choose an existing nickname/location, then add or remove configured fuel types for a station, or remove the station. After a successful change the station list stays open so you can continue managing the same location.
- **Delete location** - remove an entire nickname/location, including its configured station entities and cheapest-price entities, after confirmation.

When editing a station, the fuel selector shows the fuel types currently reported by FuelCheck for that station, plus any fuel types already configured for it. This avoids offering fuels that the selected station does not currently report.

If the final configured station is removed from a nickname/location, Home Assistant asks for confirmation before removing the now-empty location device and its cheapest-price entities. You can also use **Delete location** directly, including for an already-empty location.

Changes to an existing location's cheapest-fuel settings are validated against FuelCheck before they are saved. If the selected fuel, radius, location or exclusion text produces no matching prices, the form remains open and explains what to change.

Home Assistant places **Reconfigure** in the integration entry's three-dot menu, so it may not be immediately obvious to new users. Use the path above whenever you want to modify an existing NSW Fuel Check configuration.

## User Guide
This [user guide](./nsw_fuel_station.md) highlights the functionality and explains how to configure the integration once installed.

## Sensors Created
- Sensors for favorite fuel station(s) grouped by nickname/location e.g. home, work.
- Sensors for cheapest fuel near nickname/location.

## Installation
This integration is currently available as a [HACS](https://www.hacs.xyz/docs/use) custom integration. (It does, however, pass the automated quality checks for a core integration.) If you are new to HACS don't panic, it is in widespread use. HACS will prompt you when integrations are updated with new features and fixes.

### HACS Installation (recommended)

1. If you don't already have it, install [HACS](https://www.hacs.xyz/docs/use/), remembering to restart HA after installation.
2. Follow the [guide for installing custom repositories](https://hacs.xyz/docs/faq/custom_repositories/). In the repository field enter the address of this git repository "github.com/bicycleboy/nsw_tas_fuel_station".
3. Restart HA.
4. Go to Settings -> Integrations, Select NSW Fuel Check and follow the prompts to configure the integration (see also the [user guide](./nsw_fuel_station.md)).
5. Create dashboard cards (see user guide).

### Manual Installation

A manual installation involves simply copying a few python files into your Home Assistant config/custom_components directory. You will need familiarity with the command line and one of the Apps that provide access to the command line such as [terminal](https://github.com/home-assistant/addons/tree/master/ssh).

```
cd /tmp

git clone https://github.com/bicycleboy/nsw_tas_fuel_station.git

cd /config/custom_components

mv /tmp/nsw_fuel_station/custom_components/nsw_fuel_station.
```

You can of course inspect the files if you are concerned about anything.

Re-start Home Assistant.

## Removing the existing NSW Fuel Station Integration

If you already have the NSW Fuel Station core integration delete the sensor configuration from configuration.yaml (ie using File Viewer) and then reboot home assistant.  Delete lines that look like this:
```
sensor:
  - platform: nsw_fuel_station
    station_id: 18798
  - platform: nsw_fuel_station
    station_id: 18813
```
Sensor names will be similar but with a new prefix so dashboard cards will need updating.

## Removing this integration
Remove the integration in the standard way from:
Settings -> Devices and Services -> Select NSW Fuel Check Integration -> three dots -> Delete.
Delete cards from dashboards for all users.
Reboot home assistant.

## Repository Overview
This repository contains:

File | Purpose | Documentation
-- | -- | --
`.github/ISSUE_TEMPLATE` | Templates for the issue tracker | [Documentation](https://help.github.com/en/github/building-a-strong-community/configuring-issue-templates-for-your-repository)
`custom_components/nsw_tas_fuel_check/*.py` | Integration files required in your installation. |
`LICENSE` | The license file for the project. | [Documentation](https://help.github.com/en/github/creating-cloning-and-archiving-repositories/licensing-a-repository)
`pyproject.toml` | Python setup and configuration for this integration. | [Documentation](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/)
`tests/*.py` | Unit test files for each .py file without calling any real APIs. |
`README.md` | The file you are reading now. | [Documentation](https://help.github.com/en/github/writing-on-github/basic-writing-and-formatting-syntax)
`nsw_fuel_station.md` | User Guide. | [Documentation](https://help.github.com/en/github/writing-on-github/basic-writing-and-formatting-syntax)

## Debugging
To assist with any issues, or determine if the API is returning unexpected results or there is a bug, you can turn on debugging in your configuration.yaml.

```
logger:
  logs:
    custom_components.nsw_tas_fuel_station: debug
    nsw_tas_fuel: debug
```

## Contributing
Contributions and feedback welcome, please visit https://github.com/bicycleboy/nsw_tas_fuel_station, select **Issues** and choose either bug report or feature request.

It seems necessary to mention that AI was used to help create this integration. All production code generated by AI has been reviewed and understood (certainly to the point of understanding the code does not go rouge).

## Licence
This software is licensed under the MIT License. See the [LICENCE](https://github.com/bicycleboy/nsw_tas_fuel_ui/LICENCE) file for details.

