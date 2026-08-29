from django.shortcuts import render
from django.core.cache import cache
from django.db.models import F
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from .models import SearchLog, DeviceToken
from .decorators import premium_required
from .weather_service import WeatherService
from geopy.exc import GeocoderTimedOut, GeocoderServiceError
from .utils import (
    celsius_to_fahrenheit,
    convert_wind_speed,
    degrees_to_cardinal,
    get_astronomy_data,
    convert_temperature,
    flag_stale_current_high,
    _normalize_query,
    _resolve_region_to_capital,
    _REGION_CAPITALS,
)
from datetime import datetime
from collections import defaultdict
import re
import json
import pytz
import concurrent.futures


def index(request):
    """Main weather view - handles address input and displays forecast"""
    
    # Initialize variables
    forecasts = []
    current_weather = {}
    address = ""
    lat = None
    lon = None

    # Support both GET (?q=...&unit=F) and POST (form submission)
    if request.method == "GET":
        address = _normalize_query(request.GET.get("q", ""))
        unit = "F"
        if not address:
            return render(request, "core/index.html", {"unit": unit})
    else:
        address = _normalize_query(request.POST.get("address", ""))
        unit = request.POST.get("unit", "F")

        # "Use my location" submits raw GPS coordinates (app wrapper only)
        # instead of a typed address — these bypass forward geocoding below
        # and get reverse-geocoded instead. Premium-gated server-side: a
        # non-premium request that sends lat/lon is silently treated as if
        # it hadn't, rather than rejected outright, since this is a page
        # render rather than a JSON API.
        raw_lat = request.POST.get("lat", "").strip()
        raw_lon = request.POST.get("lon", "").strip()
        subscription = getattr(request.user, 'subscription', None) if request.user.is_authenticated else None
        has_premium = bool(subscription and subscription.is_active())
        if raw_lat and raw_lon and has_premium:
            try:
                candidate_lat, candidate_lon = float(raw_lat), float(raw_lon)
                if -90 <= candidate_lat <= 90 and -180 <= candidate_lon <= 180:
                    lat, lon = candidate_lat, candidate_lon
            except ValueError:
                pass

    weather_service = WeatherService()
    show_random_location_message = False

    if lat is not None and lon is not None:
        # Step 1 (coordinates path): reverse geocode GPS coordinates instead
        # of forward-geocoding a typed address.
        geocode_cache_key = f"geocode_rev_{round(lat, 3)}_{round(lon, 3)}"
        cached_geocode = cache.get(geocode_cache_key)

        if cached_geocode:
            address, location = cached_geocode['address'], cached_geocode['location']
        else:
            try:
                address, location = weather_service.get_address_from_coordinates(lat, lon)
            except (GeocoderTimedOut, GeocoderServiceError):
                return render(request, "core/index.html", {
                    "error_message": "Our location service is temporarily busy. Please try again in a moment.",
                    "error_type": "timeout",
                    "unit": unit
                })
            except Exception as e:
                print(f"ERROR reverse geocoding: {type(e).__name__}: {str(e)}")
                import traceback
                traceback.print_exc()
                return render(request, "core/index.html", {
                    "error_message": "Something went wrong. Please try again.",
                    "error_type": "general",
                    "unit": unit
                })

            if not address:
                address = "My Location"
            if location:
                cache.set(geocode_cache_key, {'address': address, 'location': location}, 2592000)  # 30 days
    else:
        # If no address provided, show error but pick a random location for fun
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
            address = _normalize_query(random.choice(random_locations))

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

    # Check if location was found. GPS coordinates are trusted even when
    # reverse geocoding can't name them — is_canada/international detection
    # below falls back to lat/lon bounds in that case.
    try:

        if not location and lat is None:
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

        # Unit is always determined by country, not form state
        unit = 'C' if cached_is_canada else 'F'

        # Recompute each request — depends on the current time, not cached state
        flag_stale_current_high(forecasts, current_weather)

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
                "uv_hourly_json": json.dumps(current_weather['uv']['hourly']) if current_weather.get('uv') else 'null',
            }
        )

    # Unit is always determined by country, not form state
    unit = 'C' if is_canada else 'F'

    if is_canada:
        # Fetch from ECCC MSC Datamart + astronomy + AQHI in parallel
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            eccc_future = executor.submit(weather_service.get_eccc_weather, lat, lon)
            astronomy_future = executor.submit(get_astronomy_data, lat, lon)
            air_quality_future = executor.submit(weather_service.get_air_quality, lat, lon, True)
            uv_future = executor.submit(weather_service.get_uv_index, lat, lon)
            eccc_result = eccc_future.result()
            astronomy = astronomy_future.result()
            air_quality = air_quality_future.result()
            uv_data = uv_future.result()

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

        if uv_data:
            try:
                local_tz = pytz.timezone(astronomy.get('timezone', 'UTC'))
                now_local = datetime.now(local_tz)
                uv_data['now_idx'] = next(
                    (i for i, h in enumerate(uv_data['hourly']) if h['hour_int'] == now_local.hour),
                    12
                )
            except Exception:
                uv_data['now_idx'] = 12
        current_weather['uv'] = uv_data

        current_weather['active_alerts'] = eccc_alerts
        current_weather['detailed_forecast'] = forecasts[0].get('detailedForecast', '') if forecasts else ''

        # Cache already-converted Celsius data for Canada
        cache.set(cache_key, {
            'forecasts': forecasts,
            'current': current_weather,
            'active_alerts': eccc_alerts,
            'state_abbrev': None,
            'is_canada': True,
            'uv': uv_data,
        }, 600)

        flag_stale_current_high(forecasts, current_weather)

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
                "uv_hourly_json": json.dumps(current_weather['uv']['hourly']) if current_weather.get('uv') else 'null',
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
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        # Submit all API calls at once
        forecast_future = executor.submit(weather_service.get_forecast, metadata["forecast"])
        alerts_future = executor.submit(weather_service.get_active_alerts, lat, lon)
        astronomy_future = executor.submit(get_astronomy_data, lat, lon)
        air_quality_future = executor.submit(weather_service.get_air_quality, lat, lon, False)
        uv_future = executor.submit(weather_service.get_uv_index, lat, lon)

        # Also get current weather in parallel (which internally calls get_nearest_station and get_current_observations)
        # Always fetch in Fahrenheit so cache stores consistent F data
        current_weather_future = executor.submit(get_current_weather, weather_service, metadata, 'F', state_abbrev)

        # Wait for all to complete and get results
        forecasts = forecast_future.result()
        active_alerts = alerts_future.result()
        astronomy = astronomy_future.result()
        air_quality = air_quality_future.result()
        uv_data = uv_future.result()
        current_weather = current_weather_future.result()

    current_weather['air_quality'] = air_quality

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

    if uv_data:
        try:
            local_tz = pytz.timezone(astronomy.get('timezone', 'UTC'))
            now_local = datetime.now(local_tz)
            uv_data['now_idx'] = next(
                (i for i, h in enumerate(uv_data['hourly']) if h['hour_int'] == now_local.hour),
                12
            )
        except Exception:
            uv_data['now_idx'] = 12
    current_weather['uv'] = uv_data

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
    # Always store in Fahrenheit so cached data is unit-agnostic
    cache_data = {
        'forecasts': forecasts,
        'current': current_weather,
        'active_alerts': active_alerts,
        'state_abbrev': state_abbrev,
        'is_canada': False,
        'uv': uv_data,
    }
    cache.set(cache_key, cache_data, 600)  # 10 minutes

    # Convert to Celsius after caching (cache always stores F for US locations)
    if unit == 'C':
        for period in forecasts:
            period['temperature'] = convert_temperature(
                period['temperature'],
                from_unit='F',
                to_unit='C'
            )
            period['temperatureUnit'] = 'C'
        if current_weather.get('temp') and current_weather['temp'] != "N/A":
            current_weather['temp'] = convert_temperature(
                current_weather['temp'], from_unit='F', to_unit='C'
            )
        for key in ('heat_index', 'wind_chill', 'max_temp_24h', 'min_temp_24h'):
            if current_weather.get(key) is not None:
                current_weather[key] = convert_temperature(
                    current_weather[key], from_unit='F', to_unit='C'
                )

    flag_stale_current_high(forecasts, current_weather)

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
            "uv_hourly_json": json.dumps(current_weather['uv']['hourly']) if current_weather.get('uv') else 'null',
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
        short_forecast_lower = (entry["short_forecast"] or "").lower()
        rain_in_text = any(w in short_forecast_lower for w in ("rain", "shower", "drizzle"))
        umbrella = rain_in_text or (precip_val is not None and precip_val >= 30)
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
        from django.shortcuts import redirect
        return redirect("/", permanent=True)

    address = _normalize_query(request.POST.get("address", ""))
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


@require_POST
@premium_required
def register_push(request):
    """Store an FCM device token and the user's current favorite cities.
    Premium-gated: only a logged-in user with an active subscription may
    register a token, and the token is tied to that account so it can be
    dropped again automatically if the subscription lapses."""
    import json as _json
    try:
        body = _json.loads(request.body)
        token    = (body.get('token') or '').strip()
        platform = (body.get('platform') or '').strip().lower()
        cities   = [str(c).strip() for c in (body.get('cities') or []) if c][:3]
    except (ValueError, AttributeError):
        return JsonResponse({'ok': False, 'error': 'invalid json'}, status=400)

    if not token or platform not in ('android', 'ios'):
        return JsonResponse({'ok': False, 'error': 'missing token or platform'}, status=400)

    DeviceToken.objects.update_or_create(
        token=token,
        defaults={
            'user': request.user,
            'platform': platform,
            'city_1': cities[0] if len(cities) > 0 else '',
            'city_2': cities[1] if len(cities) > 1 else '',
            'city_3': cities[2] if len(cities) > 2 else '',
        }
    )
    return JsonResponse({'ok': True})


@require_POST
@premium_required
def update_push_cities(request):
    """Update the saved cities for an existing FCM device token."""
    import json as _json
    try:
        body = _json.loads(request.body)
        token  = (body.get('token') or '').strip()
        cities = [str(c).strip() for c in (body.get('cities') or []) if c][:3]
    except (ValueError, AttributeError):
        return JsonResponse({'ok': False, 'error': 'invalid json'}, status=400)

    if not token:
        return JsonResponse({'ok': False, 'error': 'missing token'}, status=400)

    updated = DeviceToken.objects.filter(token=token, user=request.user).update(
        city_1=cities[0] if len(cities) > 0 else '',
        city_2=cities[1] if len(cities) > 1 else '',
        city_3=cities[2] if len(cities) > 2 else '',
    )
    if not updated:
        return JsonResponse({'ok': False, 'error': 'token not found'}, status=404)
    return JsonResponse({'ok': True})
