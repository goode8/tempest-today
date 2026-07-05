"""
Weather service module - handles all external API calls and data processing
"""
import requests
import os
import re
from django.core.cache import cache
from geopy.geocoders import Nominatim, Photon
from geopy.exc import GeocoderTimedOut, GeocoderServiceError

# Canadian province/territory names and abbreviations for query detection
_CA_PROVINCE_NAMES = frozenset({
    'alberta', 'british columbia', 'manitoba', 'new brunswick',
    'newfoundland', 'labrador', 'nova scotia', 'northwest territories',
    'nunavut', 'ontario', 'prince edward island', 'quebec', 'québec',
    'saskatchewan', 'yukon'
})
_CA_PROVINCE_ABBREVS = frozenset({
    'AB', 'BC', 'MB', 'NB', 'NL', 'NS', 'NT', 'NU', 'ON', 'PE', 'QC', 'SK', 'YT'
})
_CA_MAJOR_CITIES = frozenset({
    'winnipeg', 'winnepeg',
    'toronto', 'vancouver', 'calgary', 'edmonton', 'ottawa', 'montreal',
    'montréal', 'québec', 'halifax', 'saskatoon', 'regina', 'victoria',
    'kelowna', 'abbotsford', 'surrey', 'burnaby', 'richmond', 'brampton',
    'mississauga', 'hamilton', 'london', 'kitchener', 'waterloo', 'windsor',
    'sudbury', 'thunder bay', 'sault ste. marie', 'barrie', 'kingston',
    'fredericton', 'moncton', 'saint john', 'charlottetown', 'st. john\'s',
    'whitehorse', 'yellowknife', 'iqaluit', 'lethbridge', 'red deer',
    'medicine hat', 'fort mcmurray', 'prince george', 'kamloops', 'nanaimo',
})
_US_STATE_ABBREVS = frozenset({
    'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA',
    'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD',
    'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ',
    'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC',
    'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY', 'DC'
})


# WeatherAPI free tier: 1 000 000 calls/month.  We budget 1/31 of that per day
# so a single busy day can't silently exhaust the monthly allowance.
_UV_DAILY_LIMIT = 1_000_000 // 31  # ≈ 32 258


def uv_quota_allowed():
    """
    Check and increment the daily WeatherAPI UV call counter.

    Returns True if the call is within today's budget, False if the limit has
    been reached.  The counter resets automatically each UTC day via cache TTL.

    Note: Django's default local-memory cache is per-process.  If you run
    multiple workers, each worker tracks its own counter; effective combined
    limit will be higher.  Switch to a shared cache backend (database or Redis)
    to enforce the limit across all workers.
    """
    from django.utils import timezone
    today = timezone.now().strftime('%Y-%m-%d')
    key = f'uv_daily_calls_{today}'

    if (cache.get(key) or 0) >= _UV_DAILY_LIMIT:
        return False

    try:
        cache.incr(key)
    except ValueError:
        # Key doesn't exist yet — initialise it with a 25 h TTL so it
        # definitely outlives the calendar day before auto-expiring.
        cache.set(key, 1, 90_000)

    return True


_CA_POSTAL_RE = re.compile(
    r'^[ABCEGHJ-NPRSTVXY]\d[ABCEGHJ-NPRSTVWXYZ][\s-]?\d[ABCEGHJ-NPRSTVWXYZ]\d$',
    re.IGNORECASE
)


def _is_canadian_query(address):
    """Return True if the address string likely refers to a Canadian location."""
    lower = address.lower().strip()
    if 'canada' in lower:
        return True
    # Canadian postal code format: A1A 1A1 or A1A1A1
    if _CA_POSTAL_RE.match(address.strip()):
        return True
    for name in _CA_PROVINCE_NAMES:
        if name in lower:
            return True
    tokens = re.split(r'[\s,]+', address.strip())
    if any(t.upper() in _CA_PROVINCE_ABBREVS for t in tokens):
        return True
    # Match major Canadian cities (exact or as the leading part of the query)
    for city in _CA_MAJOR_CITIES:
        if lower == city or lower.startswith(city + ' ') or lower.startswith(city + ','):
            return True
    return False


class LocationIQResult:
    """Simple location object to match Geopy's interface"""
    def __init__(self, lat, lon, display_name, address_data):
        self.latitude = lat
        self.longitude = lon
        self.address = display_name
        self.raw = {'address': address_data}


def _uv_label(v):
    if v <= 2:  return 'Low'
    if v <= 5:  return 'Moderate'
    if v <= 7:  return 'High'
    if v <= 10: return 'Very High'
    return 'Extreme'

def _uv_color(v):
    if v <= 2:  return '#3ea72d'
    if v <= 5:  return '#c8a000'
    if v <= 7:  return '#f18b00'
    if v <= 10: return '#e53210'
    return '#b567a4'


class WeatherService:
    """Handles all National Weather Service API interactions"""

    def __init__(self, locationiq_api_key=None):
        self.headers = {"User-Agent": "tempesttoday.pythonanywhere.com (tempesttoday@gmail.com)"}
        self.base_url = "https://api.weather.gov"
        self.locationiq_api_key = locationiq_api_key or os.getenv('LOCATIONIQ_API_KEY')

    def get_location_coordinates(self, address, timeout=10):
        """
        Convert address to coordinates using geocoding.
        Detects Canadian queries and biases geocoding accordingly.
        Priority: LocationIQ (primary) -> Nominatim (fallback) -> Photon (fallback 2)

        Returns: tuple (lat, lon, location_obj) or (None, None, None) if failed
        """
        # Check if it looks like a US ZIP code (5 digits or 5+4 format)
        zip_pattern = re.match(r'^\d{5}(-\d{4})?$', address.strip())
        is_zip = bool(zip_pattern)

        if is_zip:
            zip_num = int(address.strip()[:5])
            if zip_num < 501 or zip_num > 99950:
                return None, None, None

        # Detect Canadian queries (province names/abbreviations or "canada")
        is_canadian = not is_zip and _is_canadian_query(address)

        # An explicit trailing US state code beats the fuzzy "known Canadian
        # city name" guess above (e.g. "windsor ca" naming Windsor, CA in
        # Sonoma County, not Windsor, Ontario just because "windsor" is also a
        # major Canadian city). No US state code collides with an actual
        # Canadian province code, so this is unambiguous.
        if is_canadian:
            trailing = re.split(r'[,\s]+', address.strip())[-1].upper()
            if trailing in _US_STATE_ABBREVS:
                is_canadian = False

        # Normalize Canadian postal codes to standard format: "b1p7c1" -> "B1P 7C1"
        if is_canadian and _CA_POSTAL_RE.match(address.strip()):
            raw = re.sub(r'[\s-]', '', address.strip()).upper()
            address = f"{raw[:3]} {raw[3:]}"

        # Normalize common Canadian province abbreviation aliases so geocoders
        # get the official two-letter code (e.g. "QU" and "PQ" both mean QC).
        _CA_PROV_ALIASES = {'qu': 'QC', 'pq': 'QC', 'nf': 'NL', 'pei': 'PE'}
        if is_canadian:
            tokens = re.split(r'([\s,]+)', address)
            new_tokens = [_CA_PROV_ALIASES.get(t.lower(), t) if re.fullmatch(r'[A-Za-z]{2,3}', t) else t for t in tokens]
            address = ''.join(new_tokens)

        # === CANADIAN POSTAL CODE: geocoder.ca (most reliable for CA postal codes) ===
        if is_canadian and _CA_POSTAL_RE.match(address.strip()):
            try:
                compact = address.replace(' ', '')
                resp = requests.get(
                    'https://geocoder.ca/',
                    params={'postal': compact, 'json': 1},
                    timeout=timeout,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    lat = float(data.get('latt', 0) or 0)
                    lon = float(data.get('longt', 0) or 0)
                    if lat and lon:
                        std = data.get('standard', {})
                        city = std.get('city', '')
                        prov = std.get('prov', '')
                        address_parts = {
                            'country_code': 'CA',
                            'state': prov,
                            'city': city,
                            'country': 'Canada',
                        }
                        location = LocationIQResult(lat, lon, f"{city}, {prov}, Canada", address_parts)
                        print(f"✓ geocoder.ca (CA): {address} -> {lat}, {lon} ({city}, {prov})")
                        return lat, lon, location
            except Exception as e:
                print(f"geocoder.ca error: {e}")

        # === PRIMARY: Try LocationIQ first (if API key available) ===
        # Skip LocationIQ for US ZIP codes — its postal centroid data is unreliable
        # (e.g. 94110 maps to the bay). Nominatim handles US ZIPs more accurately.
        if self.locationiq_api_key and not is_zip:
            try:
                # If the query ends in a state/province abbreviation, use a
                # structured query so it's a hard filter — LocationIQ's freeform
                # search ranks by importance and can ignore the trailing code
                # entirely (e.g. "ortonville mi" -> Ortonville, MN, which is
                # more prominent than Ortonville, MI; or "windsor on" -> treating
                # "on" as a stopword instead of the Ontario code).
                trailing_parts = re.split(r'[,\s]+', address.strip())
                trailing_code = trailing_parts[-1].upper() if len(trailing_parts) >= 2 else None

                if is_canadian and trailing_code in _CA_PROVINCE_ABBREVS:
                    params = {
                        'key': self.locationiq_api_key,
                        'city': ' '.join(trailing_parts[:-1]),
                        'state': trailing_code,
                        'country': 'ca',
                        'format': 'json',
                        'limit': 1,
                        'addressdetails': 1,
                    }
                elif not is_canadian and trailing_code in _US_STATE_ABBREVS:
                    params = {
                        'key': self.locationiq_api_key,
                        'city': ' '.join(trailing_parts[:-1]),
                        'state': trailing_code,
                        'country': 'us',
                        'format': 'json',
                        'limit': 1,
                        'addressdetails': 1,
                    }
                else:
                    if is_canadian:
                        query = address if 'canada' in address.lower() else f"{address}, Canada"
                        country_param = 'ca'
                    elif is_zip:
                        query = address
                        country_param = 'us'
                    else:
                        query = address
                        country_param = None

                    params = {
                        'key': self.locationiq_api_key,
                        'q': query,
                        'format': 'json',
                        'limit': 1,
                        'addressdetails': 1,
                    }
                    if country_param:
                        params['countrycodes'] = country_param

                response = requests.get(
                    "https://us1.locationiq.com/v1/search", params=params, timeout=timeout
                )

                if response.status_code == 200:
                    data = response.json()
                    if data:
                        result = data[0]
                        lat = float(result['lat'])
                        lon = float(result['lon'])
                        raw_addr = result.get('address', {})
                        cc = raw_addr.get('country_code', '').upper()
                        address_parts = {
                            'country_code': cc,
                            'state': raw_addr.get('state'),
                            'ISO3166-2-lvl4': (
                                f"US-{raw_addr.get('state')}"
                                if raw_addr.get('state') and cc == 'US' else None
                            ),
                            'country': raw_addr.get('country', ''),
                        }
                        location = LocationIQResult(lat, lon, result.get('display_name'), address_parts)
                        print(f"✓ LocationIQ ({cc}): {address} -> {lat}, {lon}")
                        return lat, lon, location

                print(f"LocationIQ failed (status {response.status_code}), trying Nominatim...")

            except Exception as e:
                print(f"LocationIQ error: {e}, trying Nominatim...")

        # === FALLBACK 1: Nominatim ===
        try:
            nom = Nominatim(user_agent="my_weather_app")

            if is_zip:
                location = nom.geocode(
                    query={'postalcode': address.strip(), 'country': 'us'},
                    timeout=timeout, exactly_one=True, addressdetails=True
                )
                # Validate the result is actually in the US — Nominatim can
                # return a plausible-looking but wrong match (e.g. DC) when
                # the ZIP doesn't exist.
                if location:
                    cc = (location.raw or {}).get('address', {}).get('country_code', '').upper()
                    if cc != 'US':
                        print(f"✗ Nominatim returned non-US result for ZIP {address} ({cc}), discarding")
                        location = None
            elif is_canadian:
                if _CA_POSTAL_RE.match(address.strip()):
                    location = nom.geocode(
                        query={'postalcode': address.strip(), 'country': 'ca'},
                        timeout=timeout, exactly_one=True, addressdetails=True
                    )
                else:
                    location = nom.geocode(
                        f"{address}, Canada",
                        timeout=timeout, exactly_one=True,
                        addressdetails=True, country_codes='ca'
                    )
            else:
                parts = re.split(r'[,\s]+', address.strip())
                if len(parts) >= 2 and parts[-1].upper() in _US_STATE_ABBREVS:
                    location = nom.geocode(
                        query={'city': ' '.join(parts[:-1]), 'state': parts[-1].upper(), 'country': 'us'},
                        timeout=timeout, exactly_one=True, addressdetails=True
                    )
                else:
                    location = nom.geocode(
                        f"{address}, USA",
                        timeout=timeout, exactly_one=True,
                        addressdetails=True, country_codes='us'
                    )

            if location:
                print(f"✓ Nominatim: {address} -> {location.latitude}, {location.longitude}")
                return location.latitude, location.longitude, location

        except (GeocoderTimedOut, GeocoderServiceError) as e:
            print(f"Nominatim error: {e}, trying Photon...")
        except Exception as e:
            print(f"Nominatim error: {e}, trying Photon...")

        # === FALLBACK 2: Photon (non-ZIP only) ===
        if not is_zip:
            try:
                photon = Photon(user_agent="my_weather_app")
                query = f"{address}, Canada" if is_canadian else f"{address}, USA"
                location = photon.geocode(query, timeout=timeout)
                if location:
                    print(f"✓ Photon: {address} -> {location.latitude}, {location.longitude}")
                    return location.latitude, location.longitude, location
            except Exception as e:
                print(f"Photon error: {e}")

        print(f"✗ All geocoders failed for: {address}")
        return None, None, None

    def get_metadata(self, lat, lon):
        """
        Get NWS metadata for coordinates (includes forecast URL, stations URL)

        Returns: dict with properties or None
        """
        url = f"{self.base_url}/points/{lat},{lon}"
        response = requests.get(url, headers=self.headers)
        data = response.json()

        return data.get("properties")

    def get_forecast(self, forecast_url):
        """
        Get weather forecast from NWS

        Returns: list of forecast periods
        """
        response = requests.get(forecast_url, headers=self.headers)
        data = response.json()

        return data.get("properties", {}).get("periods", [])

    def get_nearest_station(self, stations_url):
        """
        Get the nearest weather station info

        Returns: tuple (station_id, station_name, state_abbrev) or (None, None, None)
        """
        response = requests.get(stations_url, headers=self.headers)
        data = response.json()

        features = data.get("features", [])
        if features:
            props = features[0]["properties"]
            station_id = props.get("stationIdentifier")
            station_name = props.get("name")

            # Extract state from station timezone (e.g., "America/New_York" -> use ID parsing)
            # OR use the stationIdentifier (first letter often indicates region)
            # Better: parse from the name if it has comma-state format
            # OR get from timeZone field

            # Try to extract state from the station's timeZone or name
            state = None

            # Method 1: Some stations have state in parentheses in name
            # e.g., "Seattle-Tacoma International Airport (SEA)"
            # Method 2: Use the timeZone to infer state (not reliable)
            # Method 3: Parse from station identifier (K prefix = continental US)

            # For now, we'll leave state extraction to be handled by the geocoded location
            # The NWS API doesn't consistently provide state abbreviations

            return station_id, station_name, None

        return None, None, None

    def get_current_observations(self, station_id):
        """
        Get current weather observations from a station

        Returns: dict of observation properties
        """
        url = f"{self.base_url}/stations/{station_id}/observations/latest"
        response = requests.get(url, headers=self.headers)
        data = response.json()

        return data.get("properties", {})

    def get_active_alerts(self, lat, lon):
        """
        Get active weather alerts for coordinates

        Returns: list of alert dicts
        """
        url = f"{self.base_url}/alerts/active?point={lat},{lon}"
        response = requests.get(url, headers=self.headers)
        data = response.json()

        alerts = []
        for feature in data.get("features", []):
            props = feature.get("properties", {})
            alerts.append({
                "event": props.get("event"),
                "severity": props.get("severity"),
                "headline": props.get("headline"),
                "description": props.get("description"),
                "instruction": props.get("instruction"),
                "urgency": props.get("urgency")
            })

        return alerts

    # ── ECCC / MSC GeoMet OGC API (Canada) ───────────────────────────────────

    def get_eccc_weather(self, lat, lon):
        """
        Fetch weather from the MSC GeoMet OGC API citypageweather-realtime collection.
        Queries a bbox around the coordinates and picks the nearest city.
        Temperatures returned in Fahrenheit (converted from Celsius) for cache
        consistency; wind speed in km/h.
        Returns (forecasts, current_weather) or (None, None) if unavailable.
        """
        delta = 1.5  # degrees — wide enough to find a city, narrow enough to be fast
        bbox = f"{lon - delta},{lat - delta},{lon + delta},{lat + delta}"
        url = "https://api.weather.gc.ca/collections/citypageweather-realtime/items"
        try:
            resp = requests.get(url, params={'f': 'json', 'limit': 20, 'bbox': bbox},
                                headers=self.headers, timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"ECCC OGC API error: {e}")
            return None, None, None

        features = data.get('features', [])
        if not features:
            print(f"ECCC OGC API: no cities found near {lat},{lon}")
            return None, None, None

        # Pick the feature closest to the target coordinates
        def _dist(f):
            c = f.get('geometry', {}).get('coordinates', [0, 0])
            return (c[1] - lat) ** 2 + (c[0] - lon) ** 2

        feature = min(features, key=_dist)
        city_name = feature.get('properties', {}).get('name', {}).get('en', 'Unknown')
        print(f"✓ ECCC nearest city: {city_name}")
        return self._parse_eccc_json(feature.get('properties', {}))

    def _parse_eccc_json(self, props):
        """
        Parse an ECCC citypageweather-realtime feature properties dict.
        Returns (forecasts, current_weather) with temperatures in Fahrenheit,
        wind speed in km/h.
        """
        def _en(obj, *keys):
            """Walk nested dicts, always taking the 'en' branch at the leaf."""
            for key in keys:
                if not isinstance(obj, dict):
                    return None
                obj = obj.get(key)
            if isinstance(obj, dict):
                return obj.get('en')
            return obj

        def _c_to_f(c):
            return round(c * 9 / 5 + 32) if c is not None else None

        def _pop_from_text(text):
            """Extract precipitation probability from ECCC text (e.g. '60 per cent')."""
            if not text:
                return None
            m = re.search(r'(\d+)\s*per\s*cent', text.lower())
            if m:
                return int(m.group(1))
            m = re.search(r'(\d+)\s*%', text)
            return int(m.group(1)) if m else None

        # --- Current conditions ---
        cc = props.get('currentConditions', {})
        station_name = _en(cc, 'station', 'value') or 'ECCC Station'
        description = _en(cc, 'condition') or None
        temp_c = _en(cc, 'temperature', 'value')
        temp_f = _c_to_f(temp_c)
        humidity = _en(cc, 'relativeHumidity', 'value')
        if humidity is not None:
            humidity = int(humidity)
        wind_speed_kmh = _en(cc, 'wind', 'speed', 'value')
        if wind_speed_kmh is not None:
            try:
                wind_speed_kmh = int(wind_speed_kmh)
            except (ValueError, TypeError):
                wind_speed_kmh = 0
        wind_dir = _en(cc, 'wind', 'direction', 'value') or None
        wind_chill_c = _en(cc, 'windChill', 'value')
        wind_chill_f = _c_to_f(wind_chill_c)
        humidex_c = _en(cc, 'humidex', 'value')
        humidex_f = _c_to_f(humidex_c)

        current_weather = {
            "temp": temp_f,
            "description": description,
            "station": None,
            "station_full_name": station_name,
            "wind_speed_mph": wind_speed_kmh,
            "wind_label": "km/h",
            "current_wind_direction": wind_dir,
            "humidity": humidity,
            "heat_index": humidex_f,
            "wind_chill": wind_chill_f,
            "max_temp_24h": None,
            "min_temp_24h": None,
            "precip_1h": None,
            "precip_1h_mm": None,
        }

        # --- Forecasts ---
        forecasts = []
        for fc in props.get('forecastGroup', {}).get('forecasts', []):
            period_name = _en(fc, 'period', 'textForecastName') or ''
            if not period_name:
                continue

            is_daytime = 'night' not in period_name.lower()

            temps = fc.get('temperatures', {}).get('temperature', [])
            temp_c = temps[0].get('value', {}).get('en') if temps else None
            temp_f = _c_to_f(temp_c)

            detailed = _en(fc, 'textSummary') or ''
            short = _en(fc, 'abbreviatedForecast', 'textSummary') or detailed

            cloud_text = _en(fc, 'cloudPrecip') or ''
            pop = _pop_from_text(cloud_text) or _pop_from_text(detailed)

            wind_periods = fc.get('winds', {}).get('periods', [])
            major = next((w for w in wind_periods if _en(w, 'rank') == 'major'),
                         wind_periods[0] if wind_periods else None)
            wind_speed = int(_en(major, 'speed', 'value')) if major and _en(major, 'speed', 'value') is not None else None

            forecasts.append({
                "name": period_name,
                "isDaytime": is_daytime,
                "temperature": temp_f,
                "temperatureUnit": "F",
                "shortForecast": short,
                "detailedForecast": detailed,
                "probabilityOfPrecipitation": {"value": pop},
                "windSpeed": f"{wind_speed} km/h" if wind_speed is not None else "",
                "windDirection": "",
                "relativeHumidity": {"value": None},
                "temperatureTrend": None,
            })

        # --- Warnings ---
        active_alerts = []
        colour_to_severity = {
            'red': 'Extreme',
            'orange': 'Severe',
            'yellow': 'Moderate',
            'blue': 'Minor',
            'green': 'Minor',
        }
        for w in props.get('warnings', []):
            raw_desc = w.get('description', {}).get('en', '')
            colour = w.get('alertColourLevel', {}).get('en', '').lower()
            url = w.get('url', {}).get('en', '')
            severity = colour_to_severity.get(colour, 'Moderate')

            # "YELLOW WARNING - WIND" → "Wind Warning"
            # Strip the colour prefix if present, then title-case
            event = raw_desc
            for c in ('red', 'orange', 'yellow', 'blue', 'green'):
                prefix = f'{c} warning - '.upper()
                if event.upper().startswith(prefix):
                    event = event[len(prefix):].title() + ' Warning'
                    break
                prefix2 = f'{c} watch - '.upper()
                if event.upper().startswith(prefix2):
                    event = event[len(prefix2):].title() + ' Watch'
                    break
                prefix3 = f'{c} advisory - '.upper()
                if event.upper().startswith(prefix3):
                    event = event[len(prefix3):].title() + ' Advisory'
                    break
            else:
                event = raw_desc.title()

            if raw_desc:
                active_alerts.append({
                    'event': event,
                    'severity': severity,
                    'headline': event,
                    'description': 'Full details are available on the Environment Canada website.',
                    'instruction': 'Follow guidance from local authorities and Environment Canada.',
                    'urgency': 'Expected',
                    'url': url,
                })

        return forecasts, current_weather, active_alerts

    # ── Air Quality ────────────────────────────────────────────────────────────

    def get_uv_index(self, lat, lon):
        """
        Fetch UV index data from WeatherAPI for the given coordinates.
        Returns a dict with current UV, label, color, peak, peak_time, hourly list,
        now_idx, sunrise_hour, and sunset_hour — or None if unavailable.
        """
        cache_key = f'uv_{round(lat, 2)}_{round(lon, 2)}'
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        if not uv_quota_allowed():
            return None

        api_key = os.getenv('WEATHERAPI_KEY')
        if not api_key:
            return None

        try:
            resp = requests.get(
                'https://api.weatherapi.com/v1/forecast.json',
                params={'key': api_key, 'q': f'{lat},{lon}', 'days': 1},
                timeout=8,
            )
            resp.raise_for_status()
            data = resp.json()

            current_uv = data['current']['uv']

            hourly_list = []
            for entry in data['forecast']['forecastday'][0]['hour']:
                # time format: "2026-05-29 14:00"
                time_str = entry['time']
                hour_int = int(time_str.split(' ')[1].split(':')[0])
                if hour_int == 0:
                    label = '12 am'
                elif hour_int < 12:
                    label = f'{hour_int} am'
                elif hour_int == 12:
                    label = '12 pm'
                else:
                    label = f'{hour_int - 12} pm'
                hourly_list.append({
                    'time': label,
                    'uv': float(entry['uv']),
                    'hour_int': hour_int,
                })

            peak_entry = max(hourly_list, key=lambda h: h['uv'])
            peak_uv = peak_entry['uv']
            peak_time_label = peak_entry['time']

            result = {
                'current': current_uv,
                'label': _uv_label(current_uv),
                'color': _uv_color(current_uv),
                'peak': peak_uv,
                'peak_time': peak_time_label,
                'hourly': hourly_list,
            }
            cache.set(cache_key, result, 3600)
            return result
        except Exception as e:
            print(f'WeatherAPI UV error: {e}')
            return None

    def get_air_quality(self, lat, lon, is_canada):
        """
        Fetch current air quality for the given coordinates.
        US: AQI from AirNow EPA (requires AIRNOW_API_KEY).
        Canada: AQHI from ECCC MSC GeoMet (no key needed).
        Returns a dict with 'label', 'value', 'category', and (US only) 'pollutant',
        or None if unavailable.
        """
        if is_canada:
            return self._get_aqhi(lat, lon)
        return self._get_aqi(lat, lon)

    def _get_aqi(self, lat, lon):
        """Fetch US AQI from AirNow EPA. Returns dict or None."""
        api_key = os.getenv('AIRNOW_API_KEY')
        if not api_key:
            return None
        cache_key = f"airnow_{round(lat, 2)}_{round(lon, 2)}"
        try:
            url = "https://www.airnowapi.org/aq/observation/latLong/current/"
            params = {
                'format': 'application/json',
                'latitude': lat,
                'longitude': lon,
                'distance': 25,
                'API_KEY': api_key,
            }
            resp = requests.get(url, params=params, headers=self.headers, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if not data:
                return None
            # AirNow returns one entry per pollutant; report the highest AQI
            best = max(data, key=lambda x: x.get('AQI', 0))
            aqi_value = best.get('AQI', 0) or 0
            aqi_color_map = {
                1: '#00c853',  # Good
                2: '#d4b800',  # Moderate
                3: '#e65100',  # Unhealthy for Sensitive Groups
                4: '#b71c1c',  # Unhealthy
                5: '#6a1b9a',  # Very Unhealthy
                6: '#4a0072',  # Hazardous
            }
            cat_num = best.get('Category', {}).get('Number', 1)
            result = {
                'label': 'AQI',
                'value': aqi_value,
                'category': best.get('Category', {}).get('Name', ''),
                'pollutant': best.get('ParameterName', ''),
                'color': aqi_color_map.get(cat_num, '#00c853'),
            }
            cache.set(cache_key, result, 3600)  # cache for 1 hour
            return result
        except Exception as e:
            print(f"AirNow API error: {e}")
            cached = cache.get(cache_key)
            if cached:
                print(f"AirNow: returning cached AQI for {lat},{lon}")
            return cached

    def _get_aqhi(self, lat, lon):
        """Fetch Canadian AQHI from ECCC MSC GeoMet. Returns dict or None."""
        delta = 1.0
        bbox = f"{lon - delta},{lat - delta},{lon + delta},{lat + delta}"
        try:
            url = "https://api.weather.gc.ca/collections/aqhi-observations-realtime/items"
            resp = requests.get(
                url,
                params={'f': 'json', 'limit': 10, 'bbox': bbox, 'latest': 'true'},
                headers=self.headers,
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            features = data.get('features', [])
            if not features:
                return None
            # Pick nearest feature to target coordinates
            def _dist(f):
                c = f.get('geometry', {}).get('coordinates', [0, 0])
                return (c[1] - lat) ** 2 + (c[0] - lon) ** 2
            feature = min(features, key=_dist)
            props = feature.get('properties', {})
            aqhi_value = props.get('aqhi')
            if aqhi_value is None:
                return None
            try:
                v = float(aqhi_value)
                if v <= 3:
                    category = 'Low'
                    color = '#0288d1'
                elif v <= 6:
                    category = 'Moderate'
                    color = '#d4a000'
                elif v <= 10:
                    category = 'High'
                    color = '#b71c1c'
                else:
                    category = 'Very High'
                    color = '#6a1b9a'
                aqhi_value = int(round(v)) if v == int(v) else v
            except (ValueError, TypeError):
                category = ''
                color = ''
            return {
                'label': 'AQHI',
                'value': aqhi_value,
                'category': category,
                'color': color,
            }
        except Exception as e:
            print(f"ECCC AQHI API error: {e}")
            return None
