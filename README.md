# NSW Fuel Check Integration

Home Assistant Integration for the NSW Government Fuel Check API

## Features
- Allows users to include NSW and Tasmania fuel prices into their home assistant dashboards and automations.
- This 2026 update allows the user to configure the integration via the user interface vs configuration.yaml.

## Sensors
- Sensors for favorite fuel station(s)
- Sensors for cheapest fuel near location

## Example Cards for Your Home Assistant Dashboard

![example cards](./images/example_cards.png)

[Example card yaml](https://github.com/bicycleboy/nsw_tas_fuel_station/blob/main/example_cards.yaml)

## Installation
Prior to release, install the Terminal add on, then git clone to the home assistant config/custom_components directory:

cd /tmp

git clone https://github.com/bicycleboy/nsw_tas_fuel_station.git

cd /config/custom_components

mv /tmp/nsw_fuel_station/custom_components/nsw_fuel_station .

## Configuring NSW Fuel Check in the Home Assistant User Interface
See [usage](./nsw_fuel_station.md).

## Repository Overview
This repository contains:

File | Purpose | Documentation
-- | -- | --
`.github/ISSUE_TEMPLATE/*.yml` | Templates for the issue tracker | [Documentation](https://help.github.com/en/github/building-a-strong-community/configuring-issue-templates-for-your-repository)
`custom_components/*.py` | Integration files, this is where everything happens. |
`LICENSE` | The license file for the project. | [Documentation](https://help.github.com/en/github/creating-cloning-and-archiving-repositories/licensing-a-repository)
`pyproject.toml` | Python setup and configuration for this integration. | [Documentation](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/)
`tests/*.py` | Unit test files for each .py file without calling any real APIs. |
`README.md` | The file you are reading now. | [Documentation](https://help.github.com/en/github/writing-on-github/basic-writing-and-formatting-syntax)



## Contributing
Contributions and feedback welcome, please visit https://github.com/bicycleboy/nsw_tas_fuel_ui, select **Issues** and choose either bug report or feature request.

## Licence
This software is licensed under the MIT License. See the [LICENCE](https://github.com/bicycleboy/nsw_tas_fuel_ui/LICENCE) file for details.

