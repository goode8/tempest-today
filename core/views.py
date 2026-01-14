from django.shortcuts import render
from django.core.cache import cache
from .weather_service import WeatherService
from geopy.exc import GeocoderTimedOut, GeocoderServiceError
from .utils import (
    celsius_to_fahrenheit,
    convert_wind_speed,
    degrees_to_cardinal,
    get_astronomy_data,
    convert_temperature
)
from datetime import datetime
import pytz
import concurrent.futures


def index(request):
    """Main weather view - handles address input and displays forecast"""
    
    # Initialize variables
    forecasts = []
    current_weather = {}
    address = ""
    unit = request.POST.get("unit", "F")  # Get selected unit, default to F
    
    # Only process if form was submitted
    if request.method != "POST":
        return render(request, "core/index.html", {"unit": unit})
    
    address = request.POST.get("address")
    weather_service = WeatherService()
    
    # If no address provided, show error but pick a random location for fun
    show_random_location_message = False
    if not address or address.strip() == "":
        show_random_location_message = True
        # Pick a random interesting US city
        import random
        random_locations = [
            "Miami, FL",
            "Seattle, WA", 
            "Denver, CO",
            "Portland, ME",
            "Austin, TX",
            "Chicago, IL",
            "Phoenix, AZ",
            "Honolulu, HI",
            "Anchorage, AK",
            "Boston, MA",
            "San Francisco, CA",
            "New Orleans, LA",
            "Key West, FL",
            "Fargo, ND",
            "Las Vegas, NV",
            "Portland, OR",
            "Nashville, TN",
            "Minneapolis, MN",
            "San Diego, CA",
            "Savannah, GA"
        ]
        address = random.choice(random_locations)

    # Step 1: Get coordinates from address
    try:
        lat, lon, location = weather_service.get_location_coordinates(address)
        # lat, lon, location = weather_service.get_location_coordinates(address)

        if not location:
            return render(request, "core/index.html", {
                "error_message": "Location not found. Please check and try again.",
                "error_type": "not_found",
                "unit": unit
            })
        
        # Check if location is in USA (rough check using country code or bounds)
        # Geopy location objects have address components
        if location and hasattr(location, 'raw'):
            address_components = location.raw.get('address', {})
            country_code = address_components.get('country_code', '').upper()
            
            if country_code and country_code != 'US':
                return render(request, "core/index.html", {
                    "error_message": f"We found your location in {address_components.get('country', 'another country')}, but we currently only support USA weather.",
                    "error_type": "international",
                    "unit": unit
                })
        
    except (GeocoderTimedOut, GeocoderServiceError):
        return render(request, "core/index.html", {
            "error_message": "Our location service is temporarily busy. Please try again in a moment.",
            "error_type": "timeout",
            "unit": unit
        })
    except Exception as e:
        return render(request, "core/index.html", {
            "error_message": "Something went wrong. Please try again.",
            "error_type": "general",
            "unit": unit
        })

    # Check cache first (round coordinates to 2 decimal places for cache key)
    cache_key = f"weather_{round(lat, 2)}_{round(lon, 2)}"
    cached_data = cache.get(cache_key)
    
    if cached_data:
        # Use cached data but still convert temperatures if needed
        forecasts = cached_data['forecasts']
        current_weather = cached_data['current']
        active_alerts = cached_data['active_alerts']
        state_abbrev = cached_data['state_abbrev']
        
        # Convert forecast temperatures if unit changed
        if unit == 'C':
            for period in forecasts:
                period['temperature'] = convert_temperature(
                    period['temperature'], 
                    from_unit='F', 
                    to_unit='C'
                )
                period['temperatureUnit'] = 'C'
        
        # Convert current temp if unit changed
        if current_weather.get('temp') and current_weather['temp'] != "N/A":
            if unit == 'C':
                current_weather['temp'] = convert_temperature(
                    current_weather['temp'],
                    from_unit='F',
                    to_unit='C'
                )
        
        return render(
            request,
            "core/index.html",
            {
                "forecasts": forecasts,
                "current": current_weather,
                "address": address,
                "active_alerts": active_alerts,
                "unit": unit,
                "show_random_location_message": show_random_location_message,
                "lat": lat,
                "lon": lon
            }
        )

    # Step 2: Get NWS metadata (forecast URL, stations URL)
    metadata = weather_service.get_metadata(lat, lon)
    
    if not metadata:
        return render(request, "core/index.html", {
            "error_message": "Unable to retrieve weather data for this location.",
            "unit": unit
        })
    
    # Extract state abbreviation from geocoded location
    state_abbrev = None
    if location and hasattr(location, 'raw'):
        address_components = location.raw.get('address', {})
        # Try to get state from ISO code first (e.g., "US-CA")
        state_abbrev = address_components.get('ISO3166-2-lvl4', '').split('-')[-1] if address_components.get('ISO3166-2-lvl4') else None
        # Fallback to state field if ISO code not available
        if not state_abbrev:
            state_abbrev = address_components.get('state')
    
    # Step 3-6: Make API calls in parallel for faster loading
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        # Submit all API calls at once
        forecast_future = executor.submit(weather_service.get_forecast, metadata["forecast"])
        alerts_future = executor.submit(weather_service.get_active_alerts, lat, lon)
        astronomy_future = executor.submit(get_astronomy_data, lat, lon)
        
        # Also get current weather in parallel (which internally calls get_nearest_station and get_current_observations)
        current_weather_future = executor.submit(get_current_weather, weather_service, metadata, unit, state_abbrev)
        
        # Wait for all to complete and get results
        forecasts = forecast_future.result()
        active_alerts = alerts_future.result()
        astronomy = astronomy_future.result()
        current_weather = current_weather_future.result()
    
    # Convert forecast temperatures if needed
    if unit == 'C':
        for period in forecasts:
            period['temperature'] = convert_temperature(
                period['temperature'], 
                from_unit='F', 
                to_unit='C'
            )
            period['temperatureUnit'] = 'C'
    
    # Add astronomy data to current weather
    current_weather.update(astronomy)
    
    # Step 5b: Determine if it's currently nighttime
    try:
        local_tz = pytz.timezone(astronomy['timezone'])
        current_time = datetime.now(local_tz)
        is_night = current_time < astronomy['sunrise_dt'] or current_time > astronomy['sunset_dt']
        current_weather['is_night'] = is_night
    except:
        current_weather['is_night'] = False
    
    # Step 5c: Create moon visibility message based on position and weather
    # moon_visible = current_weather.get('moon_visible', False)
    description = current_weather.get('description') or ''
    description = description.lower() if description else ''
    
    # if moon_visible:
    #     # Moon is up - check weather conditions
    #     if any(word in description for word in ['cloud', 'overcast', 'fog', 'haze']):
    #         visibility_msg = "Moon is up but may be obscured by clouds"
    #     elif any(word in description for word in ['rain', 'storm', 'drizzle', 'snow']):
    #         visibility_msg = "Moon is up but hidden by precipitation"
    #     else:
    #         visibility_msg = "Moon should be visible"
    # else:
    #     visibility_msg = "Moon is not up right now"
    
    # current_weather['moon_visibility_msg'] = visibility_msg
    
    # Add alerts to current weather
    current_weather["active_alerts"] = active_alerts
    
    # Step 7: Add detailed forecast from first period
    if forecasts and len(forecasts) > 0:
        current_weather["detailed_forecast"] = forecasts[0].get("detailedForecast", "")
    else:
        current_weather["detailed_forecast"] = ""
    
    # Cache the weather data for 10 minutes (600 seconds)
    # Store in Fahrenheit to make unit conversion easier on cached data
    cache_data = {
        'forecasts': forecasts,
        'current': current_weather,
        'active_alerts': active_alerts,
        'state_abbrev': state_abbrev
    }
    cache.set(cache_key, cache_data, 600)  # 10 minutes
    
    return render(
        request,
        "core/index.html",
        {
            "forecasts": forecasts,
            "current": current_weather,
            "address": address,
            "active_alerts": active_alerts,
            "unit": unit,
            "show_random_location_message": show_random_location_message,
            "lat": lat,
            "lon": lon
        }
    )


def get_current_weather(weather_service, metadata, unit='F', state_abbrev=None):
    """
    Extract and process current weather observations
    
    Args:
        weather_service: WeatherService instance
        metadata: NWS metadata dict
        unit: Temperature unit ('F' or 'C')
        state_abbrev: State abbreviation from geocoded location (e.g., 'CA', 'NY')
    
    Returns: dict of current weather data (may have None values if data unavailable)
    """
    # Get nearest weather station
    station_id, station_name, _ = weather_service.get_nearest_station(
        metadata["observationStations"]
    )
    
    # Format station name with state if available
    if station_name and state_abbrev:
        station_display = f"{station_name}, {state_abbrev}"
    else:
        station_display = station_name
    
    if not station_id:
        return {
            "temp": None,
            "description": None,
            "station": None,
            "station_full_name": station_display,
            "wind_speed_mph": None,
            "wind_label": None,
            "current_wind_direction": None,
            "humidity": None,
            "heat_index": None,
            "wind_chill": None,
            "max_temp_24h": None,
            "min_temp_24h": None,
            "precip_1h": None,
            "precip_1h_mm": None,
        }
    
    # Get observations from station
    obs = weather_service.get_current_observations(station_id)
    
    # Process temperature (NWS returns Celsius, we convert to F first)
    temp_c = obs.get("temperature", {}).get("value")
    temp_f = celsius_to_fahrenheit(temp_c)
    
    # Convert to Celsius if requested, but keep None/N/A values
    if temp_f == "N/A":
        temp_display = None
    elif unit == 'C':
        temp_display = convert_temperature(temp_f, from_unit='F', to_unit='C')
        if temp_display == "N/A":
            temp_display = None
    else:
        temp_display = temp_f
    
    # Process description
    description = obs.get("textDescription")
    if not description or description == "Unknown":
        description = None
    
    # Process wind
    wind_data = obs.get("windSpeed", {})
    wind_value = wind_data.get("value")
    wind_unit_code = wind_data.get("unitCode", "")
    wind_speed_mph, wind_label = convert_wind_speed(wind_value, wind_unit_code)
    
    # Process wind direction
    wind_direction_deg = obs.get("windDirection", {}).get("value")
    wind_direction = degrees_to_cardinal(wind_direction_deg)
    
    # === NEW MODAL FIELDS ===
    
    # Humidity
    humidity = obs.get("relativeHumidity", {}).get("value")
    if humidity is not None:
        try:
            humidity = round(humidity)
        except (TypeError, ValueError):
            humidity = None
    
    # Heat Index and Wind Chill (feels like temps)
    heat_index_c = obs.get("heatIndex", {}).get("value")
    wind_chill_c = obs.get("windChill", {}).get("value")
    
    heat_index_display = None
    wind_chill_display = None
    
    if heat_index_c is not None:
        try:
            heat_index_f = celsius_to_fahrenheit(heat_index_c)
            if heat_index_f != "N/A":
                heat_index_display = round(heat_index_f) if unit == 'F' else round(heat_index_c)
        except (TypeError, ValueError):
            pass
    
    if wind_chill_c is not None:
        try:
            wind_chill_f = celsius_to_fahrenheit(wind_chill_c)
            if wind_chill_f != "N/A":
                wind_chill_display = round(wind_chill_f) if unit == 'F' else round(wind_chill_c)
        except (TypeError, ValueError):
            pass
    
    # 24-hour high/low temps
    max_temp_24h = None
    min_temp_24h = None
    
    max_temp_24h_c = obs.get("maxTemperatureLast24Hours", {}).get("value")
    min_temp_24h_c = obs.get("minTemperatureLast24Hours", {}).get("value")
    
    if max_temp_24h_c is not None:
        try:
            max_temp_24h_f = celsius_to_fahrenheit(max_temp_24h_c)
            if max_temp_24h_f != "N/A":
                max_temp_24h = round(max_temp_24h_f) if unit == 'F' else round(max_temp_24h_c)
        except (TypeError, ValueError):
            pass
    
    if min_temp_24h_c is not None:
        try:
            min_temp_24h_f = celsius_to_fahrenheit(min_temp_24h_c)
            if min_temp_24h_f != "N/A":
                min_temp_24h = round(min_temp_24h_f) if unit == 'F' else round(min_temp_24h_c)
        except (TypeError, ValueError):
            pass
    
    # Precipitation
    precip_1h_inches = None
    precip_1h_mm = obs.get("precipitationLastHour", {}).get("value")
    
    if precip_1h_mm is not None:
        try:
            # Convert mm to inches
            precip_1h_inches = round(precip_1h_mm / 25.4, 2) if precip_1h_mm > 0 else 0
        except (TypeError, ValueError):
            precip_1h_inches = None
            precip_1h_mm = None
    
    # Build current weather dict
    return {
        "temp": temp_display,
        "description": description,
        "station": station_id,
        "station_full_name": station_display,
        "wind_speed_mph": wind_speed_mph if wind_speed_mph else None,
        "wind_label": wind_label if wind_label and wind_label != "no wind data available" else None,
        "current_wind_direction": wind_direction if wind_direction else None,
        # Modal fields
        "humidity": humidity,
        "heat_index": heat_index_display,
        "wind_chill": wind_chill_display,
        "max_temp_24h": max_temp_24h,
        "min_temp_24h": min_temp_24h,
        "precip_1h": precip_1h_inches,
        "precip_1h_mm": precip_1h_mm,
    }


# def get_current_weather(weather_service, metadata, unit='F'):
#     """
#     Extract and process current weather observations
    
#     Args:
#         weather_service: WeatherService instance
#         metadata: NWS metadata dict
#         unit: Temperature unit ('F' or 'C')
    
#     Returns: dict of current weather data
#     """
#     # Get nearest weather station
#     station_id, station_name = weather_service.get_nearest_station(
#         metadata["observationStations"]
#     )
    
#     if not station_id:
#         return {}
    
#     # Get observations from station
#     obs = weather_service.get_current_observations(station_id)
    
#     # Process temperature (NWS returns Celsius, we convert to F first)
#     temp_c = obs.get("temperature", {}).get("value")
#     temp_f = celsius_to_fahrenheit(temp_c)
    
#     # Convert to Celsius if requested
#     if unit == 'C':
#         temp_display = convert_temperature(temp_f, from_unit='F', to_unit='C')
#     else:
#         temp_display = temp_f
    
#     # Process wind
#     wind_data = obs.get("windSpeed", {})
#     wind_value = wind_data.get("value")
#     wind_unit = wind_data.get("unitCode", "")
#     wind_speed_mph, wind_label = convert_wind_speed(wind_value, wind_unit)
    
#     # Process wind direction
#     wind_direction_deg = obs.get("windDirection", {}).get("value")
#     wind_direction = degrees_to_cardinal(wind_direction_deg)
    
#     # Build current weather dict
#     return {
#         "temp": temp_display,
#         "description": obs.get("textDescription", "Unknown"),
#         "station": station_id,
#         "station_full_name": station_name,
#         "wind_speed_mph": wind_speed_mph,
#         "wind_label": wind_label,
#         "current_wind_direction": wind_direction,
#         "current_wind_data": wind_data,
#         "current_wind_raw_value": wind_value,
#         "current_wind_unit_code": wind_unit,
#     }