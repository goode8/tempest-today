from django.shortcuts import render
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

    # Step 1: Get coordinates from address
    try:
        lat, lon, location = weather_service.get_location_coordinates(address)
        
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


    # Step 2: Get NWS metadata (forecast URL, stations URL)
    metadata = weather_service.get_metadata(lat, lon)
    
    if not metadata:
        return render(request, "core/index.html", {
            "error_message": "Unable to retrieve weather data for this location.",
            "unit": unit
        })
    
    # Step 3: Get forecast
    forecasts = weather_service.get_forecast(metadata["forecast"])
    
    # Convert forecast temperatures if needed
    if unit == 'C':
        for period in forecasts:
            period['temperature'] = convert_temperature(
                period['temperature'], 
                from_unit='F', 
                to_unit='C'
            )
            period['temperatureUnit'] = 'C'
    
    # Step 4: Get current observations
    current_weather = get_current_weather(weather_service, metadata, unit)
    
    # Step 5: Get astronomy data
    astronomy = get_astronomy_data(lat, lon)
    current_weather.update(astronomy)
    
    # Step 5b: Determine if it's currently nighttime
    try:
        local_tz = pytz.timezone(astronomy['timezone'])
        current_time = datetime.now(local_tz)
        is_night = current_time < astronomy['sunrise_dt'] or current_time > astronomy['sunset_dt']
        current_weather['is_night'] = is_night
    except:
        current_weather['is_night'] = False
    
    # Step 6: Get active alerts
    active_alerts = weather_service.get_active_alerts(lat, lon)
    current_weather["active_alerts"] = active_alerts
    
    return render(
        request,
        "core/index.html",
        {
            "forecasts": forecasts,
            "current": current_weather,
            "address": address,
            "active_alerts": active_alerts,
            "unit": unit
        }
    )


def get_current_weather(weather_service, metadata, unit='F'):
    """
    Extract and process current weather observations
    
    Args:
        weather_service: WeatherService instance
        metadata: NWS metadata dict
        unit: Temperature unit ('F' or 'C')
    
    Returns: dict of current weather data (may have None values if data unavailable)
    """
    # Get nearest weather station
    station_id, station_name = weather_service.get_nearest_station(
        metadata["observationStations"]
    )
    
    if not station_id:
        return {
            "temp": None,
            "description": None,
            "station": None,
            "station_full_name": station_name,
            "wind_speed_mph": None,
            "wind_label": None,
            "current_wind_direction": None
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
    
    # Build current weather dict - None values will be filled from forecast
    return {
        "temp": temp_display,
        "description": description,
        "station": station_id,
        "station_full_name": station_name,
        "wind_speed_mph": wind_speed_mph if wind_speed_mph else None,
        "wind_label": wind_label if wind_label and wind_label != "no wind data available" else None,
        "current_wind_direction": wind_direction if wind_direction else None,
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