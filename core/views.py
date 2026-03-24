from django.shortcuts import render
from django.core.cache import cache
from django.db.models import F
from .models import SearchLog
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
from collections import defaultdict
import pytz
import concurrent.futures

# Map province/state names to their capital cities so that bare region
# searches ("Quebec", "Ontario", "California") resolve to a real city.
_REGION_CAPITALS = {
    # Canadian provinces & territories
    "alberta": "Edmonton, Alberta, Canada",
    "british columbia": "Victoria, British Columbia, Canada",
    "manitoba": "Winnipeg, Manitoba, Canada",
    "new brunswick": "Fredericton, New Brunswick, Canada",
    "newfoundland": "St. John's, Newfoundland, Canada",
    "newfoundland and labrador": "St. John's, Newfoundland, Canada",
    "northwest territories": "Yellowknife, Northwest Territories, Canada",
    "nova scotia": "Halifax, Nova Scotia, Canada",
    "nunavut": "Iqaluit, Nunavut, Canada",
    "ontario": "Toronto, Ontario, Canada",
    "prince edward island": "Charlottetown, Prince Edward Island, Canada",
    "pei": "Charlottetown, Prince Edward Island, Canada",
    "quebec": "Quebec City, Quebec, Canada",
    "québec": "Quebec City, Quebec, Canada",
    "saskatchewan": "Regina, Saskatchewan, Canada",
    "yukon": "Whitehorse, Yukon, Canada",
    # US states
    "alabama": "Montgomery, AL",
    "alaska": "Juneau, AK",
    "arizona": "Phoenix, AZ",
    "arkansas": "Little Rock, AR",
    "california": "Sacramento, CA",
    "colorado": "Denver, CO",
    "connecticut": "Hartford, CT",
    "delaware": "Dover, DE",
    "florida": "Tallahassee, FL",
    "georgia": "Atlanta, GA",
    "hawaii": "Honolulu, HI",
    "idaho": "Boise, ID",
    "illinois": "Springfield, IL",
    "indiana": "Indianapolis, IN",
    "iowa": "Des Moines, IA",
    "kansas": "Topeka, KS",
    "kentucky": "Frankfort, KY",
    "louisiana": "Baton Rouge, LA",
    "maine": "Augusta, ME",
    "maryland": "Annapolis, MD",
    "massachusetts": "Boston, MA",
    "michigan": "Lansing, MI",
    "minnesota": "Saint Paul, MN",
    "mississippi": "Jackson, MS",
    "missouri": "Jefferson City, MO",
    "montana": "Helena, MT",
    "nebraska": "Lincoln, NE",
    "nevada": "Carson City, NV",
    "new hampshire": "Concord, NH",
    "new jersey": "Trenton, NJ",
    "new mexico": "Santa Fe, NM",
    "new york": "Albany, NY",
    "north carolina": "Raleigh, NC",
    "north dakota": "Bismarck, ND",
    "ohio": "Columbus, OH",
    "oklahoma": "Oklahoma City, OK",
    "oregon": "Salem, OR",
    "pennsylvania": "Harrisburg, PA",
    "rhode island": "Providence, RI",
    "south carolina": "Columbia, SC",
    "south dakota": "Pierre, SD",
    "tennessee": "Nashville, TN",
    "texas": "Austin, TX",
    "utah": "Salt Lake City, UT",
    "vermont": "Montpelier, VT",
    "virginia": "Richmond, VA",
    "washington": "Olympia, WA",
    "west virginia": "Charleston, WV",
    "wisconsin": "Madison, WI",
    "wyoming": "Cheyenne, WY",
}

def _resolve_region_to_capital(address):
    """
    If `address` is just a province or state name, return the capital city
    string instead so the geocoder gets a real city. Otherwise return as-is.
    """
    key = address.strip().lower()
    return _REGION_CAPITALS.get(key, address)


def index(request):
    """Main weather view - handles address input and displays forecast"""
    
    # Initialize variables
    forecasts = []
    current_weather = {}
    address = ""

    # Support both GET (?q=...&unit=F) and POST (form submission)
    if request.method == "GET":
        address = request.GET.get("q", "").strip()
        unit = "F"
        if not address:
            return render(request, "core/index.html", {"unit": unit})
    else:
        address = request.POST.get("address", "").strip()
        unit = request.POST.get("unit", "F")

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
            "Savannah, GA",
            "Toronto, Ontario",
            "Vancouver, BC",
            "Calgary, Alberta",
            "Montreal, Quebec",
            "Halifax, Nova Scotia",
            "Winnipeg, Manitoba",
        ]
        address = random.choice(random_locations)

    # Resolve bare province/state names to their capital city
    address = _resolve_region_to_capital(address)

    # Step 1: Get coordinates from address (with geocoding cache)
    # Cache geocoding results for 30 days to avoid hitting rate limits
    geocode_cache_key = f"geocode_{address.lower().replace(' ', '_')}"
    cached_geocode = cache.get(geocode_cache_key)
    
    if cached_geocode:
        lat, lon, location = cached_geocode['lat'], cached_geocode['lon'], cached_geocode['location']
    else:
        try:
            lat, lon, location = weather_service.get_location_coordinates(address)
            
            # Cache the geocoding result for 30 days
            if location:
                cache.set(geocode_cache_key, {
                    'lat': lat,
                    'lon': lon,
                    'location': location
                }, 2592000)  # 30 days
        except (GeocoderTimedOut, GeocoderServiceError):
            return render(request, "core/index.html", {
                "error_message": "Our location service is temporarily busy. Please try again in a moment.",
                "error_type": "timeout",
                "unit": unit
            })
        except Exception as e:
            # Log the error for debugging
            print(f"ERROR in weather lookup: {type(e).__name__}: {str(e)}")
            import traceback
            traceback.print_exc()
            
            return render(request, "core/index.html", {
                "error_message": "Something went wrong. Please try again.",
                "error_type": "general",
                "unit": unit
            })
    
    # Check if location was found
    try:

        if not location:
            return render(request, "core/index.html", {
                "error_message": "Location not found. Please check and try again.",
                "error_type": "not_found",
                "unit": unit
            })
        
        # Determine country from geocoder result or lat/lon bounds
        country_code = ''
        country_name = "another country"
        is_canada = False

        if location and hasattr(location, 'raw'):
            address_components = location.raw.get('address', {})
            country_code = address_components.get('country_code', '').upper()
            country_name = address_components.get('country', 'another country')

        # Lat/lon bounds fallback when country_code unavailable
        if not country_code and lat is not None and lon is not None:
            if 41.7 <= lat <= 83.1 and -141.0 <= lon <= -52.6:
                country_code = 'CA'
            elif not (18 <= lat <= 72 and -180 <= lon <= -65):
                country_code = 'XX'  # unknown non-US

        is_canada = (country_code == 'CA')
        is_international = country_code not in ('US', 'CA', '')

        if is_international:
            return render(request, "core/index.html", {
                "error_message": f"We found your location in {country_name}, but we currently only support USA and Canada weather.",
                "error_type": "international",
                "unit": unit
            })
    except AttributeError:
        # Cached location object might not have 'raw' attribute; fall back to bounds check
        if lat is not None and lon is not None:
            if 41.7 <= lat <= 83.1 and -141.0 <= lon <= -52.6:
                is_canada = True
            elif not (18 <= lat <= 72 and -180 <= lon <= -65):
                return render(request, "core/index.html", {
                    "error_message": "We found your location outside the USA or Canada, but we currently only support USA and Canada weather.",
                    "error_type": "international",
                    "unit": unit
                })

    # Log the search
    try:
        region = None
        if location and hasattr(location, 'raw'):
            parts = location.raw.get('address', {})
            city  = parts.get('city') or parts.get('town') or parts.get('village') or ''
            state = parts.get('ISO3166-2-lvl4', '').split('-')[-1] or parts.get('state', '')
            region = f"{city}, {state}".strip(', ')
        obj, created = SearchLog.objects.get_or_create(
            query=address,
            defaults={'region': region, 'is_random': show_random_location_message}
        )
        if not created:
            SearchLog.objects.filter(pk=obj.pk).update(count=F('count') + 1)
    except Exception:
        pass  # never let logging break the app

    # Check cache first (round coordinates to 2 decimal places for cache key)
    cache_key = f"weather_{round(lat, 2)}_{round(lon, 2)}"
    cached_data = cache.get(cache_key)
    
    if cached_data:
        forecasts = cached_data['forecasts']
        current_weather = cached_data['current']
        active_alerts = cached_data['active_alerts']
        cached_is_canada = cached_data.get('is_canada', False)

        # Canada cache is already in Celsius; US cache is in Fahrenheit
        if cached_is_canada:
            unit = 'C'
        elif unit == 'C':
            # US data: convert F → C on the fly
            for period in forecasts:
                period['temperature'] = convert_temperature(
                    period['temperature'], from_unit='F', to_unit='C'
                )
                period['temperatureUnit'] = 'C'
            if current_weather.get('temp') and current_weather['temp'] != "N/A":
                current_weather['temp'] = convert_temperature(
                    current_weather['temp'], from_unit='F', to_unit='C'
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
                "lon": lon,
                "is_canada": cached_is_canada,
            }
        )

    if is_canada:
        # Canada defaults to Celsius
        if unit == 'F':
            unit = 'C'

        # Fetch from ECCC MSC Datamart + astronomy + AQHI in parallel
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            eccc_future = executor.submit(weather_service.get_eccc_weather, lat, lon)
            astronomy_future = executor.submit(get_astronomy_data, lat, lon)
            air_quality_future = executor.submit(weather_service.get_air_quality, lat, lon, True)
            eccc_result = eccc_future.result()
            astronomy = astronomy_future.result()
            air_quality = air_quality_future.result()

        if eccc_result is None or eccc_result == (None, None, None):
            return render(request, "core/index.html", {
                "error_message": "No Environment Canada weather station found near this location. Radar is still available below.",
                "error_type": "canada_no_station",
                "unit": unit,
                "lat": lat,
                "lon": lon,
                "is_canada": True,
                "address": address,
            })

        forecasts, current_weather, eccc_alerts = eccc_result

        current_weather['air_quality'] = air_quality

        # Convert Fahrenheit → Celsius (ECCC XML parsed to F for cache consistency)
        for period in forecasts:
            period['temperature'] = convert_temperature(
                period['temperature'], from_unit='F', to_unit='C'
            )
            period['temperatureUnit'] = 'C'
        if current_weather.get('temp') is not None:
            current_weather['temp'] = convert_temperature(
                current_weather['temp'], from_unit='F', to_unit='C'
            )
        if current_weather.get('heat_index') is not None:
            current_weather['heat_index'] = convert_temperature(
                current_weather['heat_index'], from_unit='F', to_unit='C'
            )
        if current_weather.get('wind_chill') is not None:
            current_weather['wind_chill'] = convert_temperature(
                current_weather['wind_chill'], from_unit='F', to_unit='C'
            )

        current_weather.update(astronomy)
        try:
            local_tz = pytz.timezone(astronomy['timezone'])
            current_time = datetime.now(local_tz)
            is_night = current_time < astronomy['sunrise_dt'] or current_time > astronomy['sunset_dt']
            current_weather['is_night'] = is_night
        except Exception:
            current_weather['is_night'] = False

        current_weather['active_alerts'] = eccc_alerts
        current_weather['detailed_forecast'] = forecasts[0].get('detailedForecast', '') if forecasts else ''

        # Cache already-converted Celsius data for Canada
        cache.set(cache_key, {
            'forecasts': forecasts,
            'current': current_weather,
            'active_alerts': eccc_alerts,
            'state_abbrev': None,
            'is_canada': True,
        }, 600)

        return render(
            request,
            "core/index.html",
            {
                "forecasts": forecasts,
                "current": current_weather,
                "address": address,
                "active_alerts": eccc_alerts,
                "unit": unit,
                "show_random_location_message": show_random_location_message,
                "lat": lat,
                "lon": lon,
                "is_canada": True,
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
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        # Submit all API calls at once
        forecast_future = executor.submit(weather_service.get_forecast, metadata["forecast"])
        alerts_future = executor.submit(weather_service.get_active_alerts, lat, lon)
        astronomy_future = executor.submit(get_astronomy_data, lat, lon)
        air_quality_future = executor.submit(weather_service.get_air_quality, lat, lon, False)

        # Also get current weather in parallel (which internally calls get_nearest_station and get_current_observations)
        current_weather_future = executor.submit(get_current_weather, weather_service, metadata, unit, state_abbrev)

        # Wait for all to complete and get results
        forecasts = forecast_future.result()
        active_alerts = alerts_future.result()
        astronomy = astronomy_future.result()
        air_quality = air_quality_future.result()
        current_weather = current_weather_future.result()

    current_weather['air_quality'] = air_quality

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
        'state_abbrev': state_abbrev,
        'is_canada': False,
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
            "lon": lon,
            "is_canada": False,
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


_PRECIP_WORDS = [
    ("one and a half", 1.5),
    ("three quarters", 0.75),
    ("three-quarters", 0.75),
    ("a half", 0.5),
    ("half", 0.5),
    ("a quarter", 0.25),
    ("quarter", 0.25),
    ("a tenth", 0.1),
    ("tenth", 0.1),
    ("one", 1.0),
    ("two", 2.0),
    ("three", 3.0),
    ("four", 4.0),
    ("five", 5.0),
    ("six", 6.0),
    ("seven", 7.0),
    ("eight", 8.0),
    ("nine", 9.0),
    ("ten", 10.0),
]

def _parse_precip_inches(text):
    """Extract a precipitation amount in inches from NWS detailed forecast text."""
    import re
    t = text.lower()
    # Numeric range: "1 to 2 inches"
    m = re.search(r'(\d+(?:\.\d+)?)\s+to\s+(\d+(?:\.\d+)?)\s+inch', t)
    if m:
        return (float(m.group(1)) + float(m.group(2))) / 2.0
    # Single numeric: "0.5 inches", "2 inches"
    m = re.search(r'(\d+(?:\.\d+)?)\s+inch', t)
    if m:
        return float(m.group(1))
    # Word-based amounts (longer phrases checked first)
    if "inch" in t:
        for phrase, val in _PRECIP_WORDS:
            if phrase in t:
                return val
    return None


def build_7day_compact(forecasts):
    """
    Collapse NWS period-based forecasts into 7 clean day-summary dicts.
    Tracks rain (umbrella) and snow (boots) separately.
    """
    days = {}
    order = []

    for period in forecasts:
        name = period.get("name", "")
        is_day = period.get("isDaytime", True)
        temp = period.get("temperature")
        precip = None
        precip_raw = period.get("probabilityOfPrecipitation", {})
        if isinstance(precip_raw, dict):
            precip = precip_raw.get("value")

        short = period.get("shortForecast", "")
        short_lower = short.lower()
        detailed = period.get("detailedForecast", "")

        name_lower = name.lower()
        is_night_period = (
            "tonight" in name_lower
            or "overnight" in name_lower
            or ("night" in name_lower and name_lower != "night")
        )
        # If no isDaytime field (ECCC), infer from name
        if "isDaytime" not in period:
            is_day = not is_night_period

        if is_night_period:
            if "tonight" in name_lower or "overnight" in name_lower:
                day_key = "Today"
            else:
                import re as _re
                day_key = _re.sub(r'\s+night$', '', name, flags=_re.IGNORECASE).strip()
        elif name.startswith("This ") or name.startswith("Late "):
            day_key = "Today"
        elif name == "Today":
            day_key = "Today"
        else:
            day_key = name

        if day_key not in days:
            days[day_key] = {
                "label": day_key,
                "high": None,
                "low": None,
                "max_precip": None,
                "max_precip_inches": None,
                "short_forecast": "",
                "has_snow": False,
            }
            order.append(day_key)

        entry = days[day_key]

        if is_day:
            if entry["high"] is None:
                entry["high"] = temp
            if not entry["short_forecast"]:
                entry["short_forecast"] = short
        else:
            if entry["low"] is None:
                entry["low"] = temp

        if precip is not None:
            try:
                precip_int = int(precip)
                if entry["max_precip"] is None or precip_int > entry["max_precip"]:
                    entry["max_precip"] = precip_int
            except (ValueError, TypeError):
                pass

        if detailed:
            inches = _parse_precip_inches(detailed)
            if inches is not None:
                if entry["max_precip_inches"] is None or inches > entry["max_precip_inches"]:
                    entry["max_precip_inches"] = inches

        if any(word in short_lower for word in ["snow", "blizzard", "sleet", "flurr"]):
            entry["has_snow"] = True

    result = []
    for key in order:
        entry = days[key]
        precip_val = entry["max_precip"]
        has_snow = entry["has_snow"]
        umbrella = (precip_val is not None and precip_val >= 30 and not has_snow)
        inches = entry["max_precip_inches"]
        if inches is not None:
            threshold = 4.0 if has_snow else 1.0
            precip_label = "a little" if inches < threshold else "a lot"
        else:
            precip_label = None
        result.append({
            "label": entry["label"],
            "high": entry["high"],
            "low": entry["low"],
            "precip": precip_val,
            "precip_label": precip_label,
            "umbrella": umbrella,
            "snow": has_snow,
            "short_forecast": entry["short_forecast"],
        })
    return result



def compact_forecast(request):
    """
    Returns the compact 7-day HTML partial.
    Expects: POST with address and optionally unit.
    Used by the Quick Look button via fetch().
    """
    from django.http import HttpResponseBadRequest, HttpResponse
    from django.template.loader import render_to_string

    if request.method != "POST":
        return HttpResponseBadRequest("POST required")

    address = request.POST.get("address", "").strip()
    unit = request.POST.get("unit", "F")

    def render_error(msg):
        html = render_to_string("core/_compact_forecast.html", {
            "error": msg, "unit": unit,
        }, request=request)
        return HttpResponse(html)

    if not address:
        return render_error("No address provided.")

    address = _resolve_region_to_capital(address)
    weather_service = WeatherService()

    geocode_cache_key = f"geocode_{address.lower().replace(chr(32), chr(95))}"
    cached_geocode = cache.get(geocode_cache_key)

    if cached_geocode:
        lat = cached_geocode["lat"]
        lon = cached_geocode["lon"]
        location = cached_geocode["location"]
    else:
        lat, lon, location = weather_service.get_location_coordinates(address)
        if location:
            cache.set(geocode_cache_key, {"lat": lat, "lon": lon, "location": location}, 2592000)

    if not location:
        return render_error("Location not found.")

    # Detect Canada from cached geocode country_code or lat/lon bounds
    is_canada = False
    country_code = ''
    if hasattr(location, 'raw'):
        country_code = location.raw.get('address', {}).get('country_code', '').upper()
        is_canada = (country_code == 'CA')
    # Only use lat/lon bounds as fallback when country_code is truly unknown
    if not country_code and lat is not None and lon is not None:
        if 41.7 <= lat <= 83.1 and -141.0 <= lon <= -52.6:
            is_canada = True

    # Canada always uses Celsius
    if is_canada:
        unit = 'C'

    weather_cache_key = f"weather_{round(lat, 2)}_{round(lon, 2)}"
    cached_weather = cache.get(weather_cache_key)

    eccc_alerts = []
    if cached_weather:
        raw_forecasts = cached_weather["forecasts"]
        eccc_alerts = cached_weather.get('active_alerts', []) if cached_weather.get('is_canada') else []
        # Canada cache is already in Celsius; US cache needs conversion if requested
        if not cached_weather.get('is_canada', False) and unit == "C":
            for period in raw_forecasts:
                period["temperature"] = convert_temperature(
                    period["temperature"], from_unit="F", to_unit="C"
                )
    elif is_canada:
        eccc_result = weather_service.get_eccc_weather(lat, lon)
        if not eccc_result or eccc_result == (None, None, None):
            return render_error("No Environment Canada station found near this location.")
        raw_forecasts, _, eccc_alerts = eccc_result
        # Convert F → C (ECCC returns F internally)
        for period in raw_forecasts:
            period["temperature"] = convert_temperature(
                period["temperature"], from_unit="F", to_unit="C"
            )
    else:
        metadata = weather_service.get_metadata(lat, lon)
        if not metadata:
            return render_error("Weather data unavailable.")
        raw_forecasts = weather_service.get_forecast(metadata["forecast"])
        if unit == "C":
            for period in raw_forecasts:
                period["temperature"] = convert_temperature(
                    period["temperature"], from_unit="F", to_unit="C"
                )

    days = build_7day_compact(raw_forecasts)
    any_umbrella = any(d["umbrella"] for d in days)
    any_snow = any(d["snow"] for d in days)
    active_alerts = eccc_alerts if is_canada else weather_service.get_active_alerts(lat, lon)

    html = render_to_string("core/_compact_forecast.html", {
        "days": days,
        "address": address,
        "unit": unit,
        "any_umbrella": any_umbrella,
        "any_snow": any_snow,
        "active_alerts": active_alerts,
        "lat": lat,
        "lon": lon,
        "is_canada": is_canada,
    }, request=request)
    return HttpResponse(html)
