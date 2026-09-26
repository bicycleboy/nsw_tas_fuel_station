
<!-----
title: NSW Fuel Check
description: Integration for the New South Wales Fuel Check API
ha_release: 2026.5
ha_iot_class: Cloud Polling
ha_codeowners:
  - '@bicycleboy'
ha_domain: nsw_tas_fuel_station
ha_integration_type: hub
related:
  - url: https://github.com/bicycleboy/nsw_tas_fuel_station
    title: Integration Source
  - url: https://github.com/bicycleboy/nsw-fuel-api-client
    title: API Client Source
---
-->

The **NSW Fuel Check** integration is used to integrate with the NSW Government API for Fuel Prices using official mandatory reporting data from NSW Fuel Check and FuelCheck - TAS.

This integration only supports Australian states NSW, the ACT and Tasmania.

Like weather integrations, the idea is not to replace the NSW Fuel Check App but give you a glance at prices as you visit your Home Assistant dashboard.


# Prerequisites

1. Live or travel in NSW, the ACT or Tasmania.
2. Visit api.nsw.gov.au.
3. Subscribe to the FuelCheck API and create an app to obtain your API Key and Secret. Sign-up is free. The site requires an email address but does not spam you and the address
does not have to be your main email address. When prompted to create and name your app it can have any name.  Make a note of the API Key and API Secret.

The process takes all of 5 minutes, while API keys can be off putting for some, it is a once and done process and popularity presumably helps keep NSW Fuel Check funded.

![API Signup](./images/api_signup.png)

# Installation
Currently this is a custom integration, see [the readme](./README.md) for installation details.

# Configuration

## Home Zone

On the setup screen, enter your credentials.

![Credentials](./images/enter_credentials.png)

Once you have validated your key and secret you will be prompted to select fuel stations from the list of stations near your home zone. These "favorite" stations assume we are creatures of habit and typically fill up at stations that are often the cheapest near us.

![select stations](./images/select_stations.png)

Select around 1–4 stations; more can be difficult to display neatly on a dashboard. Also be aware that the API has rate limits if you choose many stations.

Sensors will be created for each station you select.  In NSW and the ACT the default search is for both Ethanol E10 and Unleaded U91. In Tasmania by default search is for Unleaded U91.

Click Submit.

## Adding Sensors to Your Dashboard

#### Selected (Favorite) Stations

Configure your dashboard cards as normal, the sensors will have names like "BP Rosedale U91", starting with the brand name. As station names can be long, you may want to configure a short custom name in cards that support it.

#### Cheapest Stations

Two additional sensors will be created:

- Cheapest Home #1
- Cheapest Home #2

The NSW Fuel Check API returns a balance between cheapest fuel and distance from your home zone. By default for NSW the integration looks for the lowest of U91 and E10 prices and for Tasmania the cheapest U91.

Note that the cheapest are *not* influenced by the favorite stations you choose. The API returns the cheapest stations based on the location, radius and selected fuel. This has the advantage that you may be alerted to a cheap price at a station that is not already one of your favorites.

![cheapest stations](./images/tile_card_find_cheapest_sensor.png)

The **tile card** is a good choice for the cheapest sensors as the tile card provides access to the additional attributes of these sensors.  In the tile card configuration under the **Content - State Content** heading, use the **Add** button to add **Station name**, **Fuel type**, and **State**.  You may want to include Last Changed which is when the API reports the fuel price was last updated.

![add state](./images/tile_card_add_state_content.png)

Since station names are often long you may wish to make the tile card full width.

![adjust size](./images/tile_card_adjust_size.png)

The sensor card and glance card may also suit your dashboard, note that not all cards currently support additional attributes.

![example cards](./images/example_cards.png)

The Markup card can be useful if you want the price loud and proud on a mobile device.

![markup cards](./images/markup.jpg)

View the [example card yaml](https://github.com/bicycleboy/nsw_tas_fuel_station/blob/main/example_cards.yaml)

# Advanced Configuration

You can create multiple locations/nicknames to support many scenarios like "Home", "Work", "Trip to Work", or "Home Unleaded", "Home Diesel".  Each location/nickname will create 2 cheapest sensors for the location and fuel type you select.

## Add new location/nickname

Use **Reconfigure** from the main integration page dropdown

**Settings -> Devices & services -> NSW Fuel Check -> top most three dots -> Reconfigure**

Select **Add new location** from the menu to create another location/nickname and select its initial favorite station(s). A nickname groups station sensors for ease of identification and work in the UI and automations.  For each location/nickname two cheapest-price sensors are created.

The location selector and search radius determine where FuelCheck searches. The selected fuel type determines the station list shown during configuration *and* the fuel searched by the cheapest sensors for that location/nickname.

You may need to adjust the location and radius as described in troubleshooting below to find your preferred stations and ensure they are included in the cheapest search.

Note that the cheapest are *not* influenced by the favorite stations you choose. The API returns the cheapest stations based on the location, radius and selected fuel. This has the advantage that you may be alerted to a cheap price at a station that is not already one of your favorites.

## Reconfigure existing settings

Configuration changes are made from the integration's main page **Reconfigure** menu:

**Settings -> Devices & services -> NSW Fuel Check -> top most three dots -> Reconfigure**

The menu provides five options:

### 1. Add new location/nickname

As described above.

### 2. Add station to existing location

Use **Add station to existing location/nickname** when you want to add another favorite station to a location/nickname that is already configured.

First choose the existing location/nickname from the dropdown.

Then choose a search location/radius and fuel type to find the station. These search choices are used to find more stations; they do not change the existing location's saved coordinates, radius or cheapest-fuel type.

### 3. Edit existing location/nickname settings

Use **Edit existing location/nickname settings** when you want to change an existing location/nicknames settings.

You can change:

- saved location and search radius for the cheapest sensors;
- cheapest-fuel search type;
- cheapest-station exclusion text.

The integration checks the proposed settings with FuelCheck before saving them. If the selected fuel, location, radius or exclusion text produces no matching prices, the change is not saved and the form explains what can be adjusted.

Changing these settings does not add or remove favorite stations.

Entering a value in the **Exclude from Cheapest results** field is designed to exclude members only stations where you are not a member, such as Costco, from the cheapest sensors. It can also used to to exclude stations that are inconvenient. Your favorite stations are not affected by this setting

### 4. Manage configured stations

Use **Manage configured stations** to select a location/nickname and then one of its configured stations.

For the selected station you can:

- add a fuel type currently reported by FuelCheck for that station;
- remove an individual configured fuel type by removing its selected chip;
- remove the entire station from that location/nickname.

After a successful station change, the station list remains open so you can continue editing other stations for the same location/nickname.

If you remove the final configured station from a location/nickname, Home Assistant asks for confirmation before also removing the now-empty location/nickname and its cheapest-price sensors.

### 5. Delete location

Use **Delete location** to remove an entire location/nickname, all of its configured favorite-station sensors, and its cheapest-price sensors. A confirmation screen is shown before deletion.

This also provides a cleanup path for an existing location/location that no longer contains any favorite stations.

# Data updates

The **NSW Fuel Check** integration polls data from the API twice a day by default.

# Known limitations

The integration currently only supports New South Wales, Canberra/the ACT and Tasmania (Australia).

Some fuel types such as EV can be selected but currently do not return any data.

Selecting less common fuel types may produce unexpected results, e.g. NSW stations included in Tasmania.

# Troubleshooting

## I cannot see the station name with the cheapest price, only "Cheapest Home 1"

#### Description

Most Lovelace cards do not support the required additional attributes which hold the station name.

#### Resolution

Use a tile card as described under **Cheapest Stations** above.

## My Cheapest Home 2 sensor is unavailable

#### Description

No price is shown, only unavailable for the second-cheapest sensor.

#### Resolution

In some locations the NSW Fuel Check API may only return 1 station.  Try changing the location for the nickname repeatedly until you get a useful list of stations, these will likely be the stations that are "surveyed" for the cheapest fuel.

If a sensor consistently shows as unavailable you can disable the sensor using [Settings > Devices & services > Entities ](https://www.home-assistant.io/docs/configuration/customizing-devices/).

## I want prices to update more frequently than twice a day

#### Description

Normally by default the NSW Fuel Check API is called every 12 hours.  Some users may prefer a more frequent update, such as just before leaving for work.

#### Resolution

Asking Home Assistant to update *any one* sensor will cause *all* favorite station sensors and *all* the cheapest sensors for **all** locations/nicknames to update.  A time based sensor example is below.

```
alias: Update Fuel Prices
description: ''
triggers:
  - trigger: time
    at: '08:00:00'
    weekday:
      - mon
      - tue
      - wed
      - thu
      - fri
conditions: []
actions:
  - action: homeassistant.update_entity
    metadata: {}
    data:
      entity_id:
        - sensor.home_cheapest_home_1
mode: single
```

**Caution** using an hourly or more frequent trigger or, say, a trigger such as leaving your home zone, may result in unnecessary API calls and you running out of your monthly free API call allowance.

## I only see one station / I am not seeing the stations I expected in the select stations list

#### Description

Your stations list is missing stations you expected to see.

#### Resolution

This can be for a number of reasons. For example, you searched for U91 but the station does not stock U91. Use **Reconfigure** and try different fuel types and locations. Try using different locations and radius settings to get all the stations you want. In some locations you may need to move the centre of the radius close to the missing station (such as on the outskirts of town) and increase the radius to include most other local stations. If you are still not seeing what you want, see "I want to know the cheapest price close to my usual routes" below. You can also turn on debugging as described in [the readme](./README.md) and check the logs for errors and details of the parameters sent to NSW Fuel Check.

## How do I remove a station or fuel type I no longer want?

#### Resolution

Open **Reconfigure -> Manage configured stations**, choose the nickname/location and then the station.

Remove an individual fuel by removing its selected fuel chip, or enable **Remove station** to remove the whole station. If it is the last station in the location, Home Assistant asks for confirmation before removing the now-empty location and its cheapest-price entities.

To remove a whole location directly, use **Reconfigure -> Delete location**.

## I just want 1 cheapest sensor / I want a sensor to cover my entire trip to work but only close to my route

#### Description

I want to summarise prices from multiple locations.
I want to know the cheapest price close to my usual routes, without cluttering my dashboard with many cards.

#### Resolution (Advanced)

1. This solution requires comfort with editing configuration.yaml.
2. Use **Reconfigure** and **Add new location/nickname** to create multiple nicknames with a small, say 5 km, radius along your route(s).  You might think ofbthis as creating a long thin search area. Select just 1 station for each location.
3. Edit your configuration.yaml and create a template sensor similar to [this example](./example_template_sensor.yaml).  You will of course need to change the sensor names to match yours or get your favorite AI to do it for you.
4. Restart HA.
5. Add the template sensor to your dashboard.  You can find example cards like the below using the template sensor [here](./example_card_template_sensor.yaml).
6. You may wish to disable any station entities created if you are not using them on your dashboard to avoid API rate limits.

![templatesensor](./images/example_card_template_sensor.png)

## I am a Diesel/Premium Petrol user, how do I find the cheapest?

#### Description

By default the cheapest sensors search for E10/U91.  Pre 2026 releases only supported E10/U91. Previous workarounds were limited to a finite set of chosen stations, whereas in later releases the cheapest stations found by NSW Fuel Check and might include unexpected stations.

#### Resolution

Use **Reconfigure -> Edit existing location/nickname settings** to change the cheapest-fuel search type for an existing location without adding another station. Choose Premium Unleaded 95/98, Diesel, or another supported fuel. The integration validates the new selection against FuelCheck before saving it.

If you have multiple vehicles you can also use **Reconfigure -> Add new location/nickname** to track multiple fuels.

Use **Reconfigure -> Manage configured stations** if you also want to add or remove fuel types for individual favorite stations.

## When I select reconfigure I get "already in progress"

#### Description

Starting a reconfigure returns an error "already in progress". This may happen when inadvertently using multiple windows or when a re-configure does not complete for some reason.


#### Resolution

Re-start Home Assistant.

# Feedback
Feedback, ideas, requests, bugs all welcome and can be made [here](https://github.com/bicycleboy/nsw_tas_fuel_station/issues).
