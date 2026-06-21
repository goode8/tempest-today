"""
Utility functions for weather data processing and astronomy calculations
"""
from astral import LocationInfo
from astral.sun import sun
from astral.moon import phase as astral_phase
import math
import re
from datetime import date, datetime, timedelta
from timezonefinder import TimezoneFinder
import pytz


# ── Region/state/province → capital city mapping ─────────────────────────────
# Allows bare region names ("Quebec", "California") to resolve to a real city.

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


def _normalize_query(q):
    q = q.lower().strip()
    q = re.sub(r'[.,;:]+\s*', ' ', q)
    q = re.sub(r'\s+', ' ', q)
    return q.strip()


def _resolve_region_to_capital(address):
    return _REGION_CAPITALS.get(address.strip().lower(), address)


def geocode_query(q):
    """
    Geocode a query string to (lat, lon, is_canada).
    Returns None if the location cannot be found or geocoding fails.
    Shares the same 30-day cache as the main view.
    """
    from django.core.cache import cache
    from .weather_service import WeatherService
    from geopy.exc import GeocoderTimedOut, GeocoderServiceError

    q = _normalize_query(q)
    q = _resolve_region_to_capital(q)

    cache_key = f"geocode_{q.lower().replace(' ', '_')}"
    cached = cache.get(cache_key)
    if cached:
        lat, lon = cached['lat'], cached['lon']
        location = cached.get('location')
    else:
        ws = WeatherService()
        try:
            lat, lon, location = ws.get_location_coordinates(q)
        except (GeocoderTimedOut, GeocoderServiceError, Exception):
            return None
        if not location:
            return None
        cache.set(cache_key, {'lat': lat, 'lon': lon, 'location': location}, 2592000)

    if lat is None or lon is None:
        return None

    is_canada = False
    try:
        cc = location.raw.get('address', {}).get('country_code', '').upper()
        is_canada = (cc == 'CA')
    except (AttributeError, TypeError):
        is_canada = (41.7 <= lat <= 83.1 and -141.0 <= lon <= -52.6)

    return lat, lon, is_canada

# ── Meeus moonrise/moonset helpers ───────────────────────────────────────────

def _jd(year, month, day):
    """Julian Day for 0h UT (Meeus Ch. 7)."""
    if month <= 2:
        year -= 1
        month += 12
    A = year // 100
    B = 2 - A + A // 4
    return int(365.25 * (year + 4716)) + int(30.6001 * (month + 1)) + day + B - 1524.5


def _theta0(jd):
    """Apparent sidereal time at Greenwich at 0h UT, degrees (Meeus Ch. 12)."""
    T = (jd - 2451545.0) / 36525.0
    return (100.4606184 + 36000.77004 * T + 0.000387933 * T * T) % 360


def _interp3(n, y0, y1, y2):
    """Three-point interpolation (Meeus eq. 3.3). n in [-1, 1]."""
    a, b = y1 - y0, y2 - y1
    return y1 + n * (a + b + n * (b - a)) / 2


def _moon_pos(jd):
    """
    Moon RA (deg), Dec (deg), horizontal parallax (deg).
    Meeus Ch. 47 low-accuracy series — good to ~1°, gives ~2 min accuracy for rise/set.
    """
    T  = (jd - 2451545.0) / 36525.0
    r  = math.radians
    Lp = (218.3164477 + 481267.88123421 * T) % 360
    D  = (297.8501921 + 445267.1114034  * T) % 360
    M  = (357.5291092 +  35999.0502909  * T) % 360
    Mp = (134.9633964 + 477198.8675055  * T) % 360
    F  = ( 93.2720950 + 483202.0175233  * T) % 360

    lon = (Lp
           + 6.289 * math.sin(r(Mp))
           - 1.274 * math.sin(r(2*D - Mp))
           + 0.658 * math.sin(r(2*D))
           - 0.214 * math.sin(r(2*Mp))
           - 0.114 * math.sin(r(2*F)))

    lat = (  5.128 * math.sin(r(F))
           + 0.280 * math.sin(r(Mp + F))
           + 0.277 * math.sin(r(Mp - F))
           + 0.173 * math.sin(r(2*D - F))
           + 0.055 * math.sin(r(2*D - Mp - F)))

    par = (0.9508
           + 0.0518 * math.cos(r(Mp))
           + 0.0095 * math.cos(r(2*D - Mp))
           + 0.0078 * math.cos(r(2*D))
           + 0.0028 * math.cos(r(2*Mp)))

    eps = 23.4393 - 0.013 * T
    b, l, e = r(lat), r(lon), r(eps)
    x = math.cos(b) * math.cos(l)
    y = math.cos(e) * math.cos(b) * math.sin(l) - math.sin(e) * math.sin(b)
    z = math.sin(e) * math.cos(b) * math.sin(l) + math.cos(e) * math.sin(b)

    ra  = math.degrees(math.atan2(y, x)) % 360
    dec = math.degrees(math.asin(z))
    return ra, dec, par


def _calc_moonrise_moonset(lat, lon, today, local_tz):
    """
    Meeus Ch. 15 — moonrise and moonset for a local calendar date.
    Anchors to LOCAL midnight so m in [0,1] always falls within the local day,
    regardless of UTC offset.
    """
    # Convert local midnight to UTC → gives the JD anchor
    local_midnight = local_tz.localize(datetime(today.year, today.month, today.day))
    lm_utc = local_midnight.astimezone(pytz.utc)
    jd_0h = _jd(lm_utc.year, lm_utc.month, lm_utc.day)
    utc_frac = (lm_utc.hour * 3600 + lm_utc.minute * 60 + lm_utc.second) / 86400.0
    jd0 = jd_0h + utc_frac

    # GMST at local midnight
    th0 = (_theta0(jd_0h) + 360.985647 * utc_frac) % 360

    phi = math.radians(lat)

    # Moon positions at local midnight ±1 local day
    ra0, dec0, _   = _moon_pos(jd0 - 1)
    ra1, dec1, par = _moon_pos(jd0)
    ra2, dec2, _   = _moon_pos(jd0 + 1)

    # Unwrap RA so interpolation doesn't jump across 0°/360°
    if ra1 < ra0: ra1 += 360
    if ra2 < ra1: ra2 += 360

    # Standard altitude for moon centre (Meeus p. 102)
    h0  = math.radians(0.7275 * par - 0.5667)
    phi_r = phi

    cos_H = (math.sin(h0) - math.sin(phi_r) * math.sin(math.radians(dec1))) / \
            (math.cos(phi_r) * math.cos(math.radians(dec1)))
    if abs(cos_H) > 1:
        # Moon circumpolar or never rises — check current altitude for visibility
        now_local = datetime.now(local_tz)
        t_frac = (now_local - local_midnight).total_seconds() / 86400.0
        ra_n, dec_n, _ = _moon_pos(jd0 + t_frac)
        lha = (th0 + 360.985647 * t_frac + lon - ra_n) % 360
        if lha > 180: lha -= 360
        alt = math.degrees(math.asin(
            math.sin(phi_r) * math.sin(math.radians(dec_n)) +
            math.cos(phi_r) * math.cos(math.radians(dec_n)) * math.cos(math.radians(lha))
        ))
        return None, None, alt > 0

    H0     = math.degrees(math.acos(cos_H))
    m0     = ((ra1 - lon - th0) / 360) % 1
    # No % 1 on m_rise/m_set — a negative or >1 value means the event falls on
    # an adjacent local day and should be returned as None for this date.
    m_rise = m0 - H0 / 360
    m_set  = m0 + H0 / 360

    # Two-iteration refinement (no % 1 — preserve which side of midnight)
    results = {}
    for label, m in [('rise', m_rise), ('set', m_set)]:
        for _ in range(2):
            ra  = _interp3(m, ra0, ra1, ra2)
            dec = _interp3(m, dec0, dec1, dec2)
            lha = (th0 + 360.985647 * m + lon - ra) % 360
            if lha > 180: lha -= 360
            sin_lha = math.sin(math.radians(lha))
            if abs(sin_lha) < 1e-6:
                break
            h = math.degrees(math.asin(
                math.sin(phi_r) * math.sin(math.radians(dec)) +
                math.cos(phi_r) * math.cos(math.radians(dec)) * math.cos(math.radians(lha))
            ))
            dm = (h - math.degrees(h0)) / (
                360 * math.cos(math.radians(dec)) * math.cos(phi_r) * sin_lha
            )
            m = m + dm
        results[label] = m

    def _to_dt(m):
        # Allow m slightly outside [0,1]: moonrises near midnight belong to
        # the adjacent local day and are needed for the 3-day display arc.
        if not (-0.5 < m < 1.5):
            return None
        return local_midnight + timedelta(seconds=round(m * 86400))

    rise_dt = _to_dt(results['rise'])
    set_dt  = _to_dt(results['set'])

    # Moon currently visible?
    now_local = datetime.now(local_tz)
    if rise_dt and set_dt:
        moon_visible = rise_dt <= now_local <= set_dt
    elif rise_dt and not set_dt:
        moon_visible = now_local >= rise_dt      # rose today, sets tomorrow
    elif set_dt and not rise_dt:
        moon_visible = now_local <= set_dt       # rose yesterday, sets today
    else:
        moon_visible = False

    return rise_dt, set_dt, moon_visible


def celsius_to_fahrenheit(temp_c):
    """Convert Celsius to Fahrenheit"""
    if temp_c is None:
        return "N/A"
    return round((temp_c * 9 / 5) + 32)


def fahrenheit_to_celsius(temp_f):
    """Convert Fahrenheit to Celsius"""
    if temp_f is None or temp_f == "N/A":
        return "N/A"
    return round((temp_f - 32) * 5 / 9)


def convert_temperature(temp, from_unit='F', to_unit='F'):
    """
    Convert temperature between F and C

    Args:
        temp: Temperature value (can be int, float, or string)
        from_unit: Original unit ('F' or 'C')
        to_unit: Target unit ('F' or 'C')

    Returns: Converted temperature
    """
    if temp == "N/A" or temp is None:
        return "N/A"

    # If units are the same, no conversion needed
    if from_unit == to_unit:
        return temp

    # Convert to int if it's a string number
    try:
        temp = float(temp)
    except (ValueError, TypeError):
        return "N/A"

    # Convert F to C
    if from_unit == 'F' and to_unit == 'C':
        return round((temp - 32) * 5 / 9)

    # Convert C to F
    if from_unit == 'C' and to_unit == 'F':
        return round((temp * 9 / 5) + 32)

    return temp


def convert_wind_speed(raw_value, unit_code):
    """
    Convert wind speed from various units to MPH

    Returns: tuple (speed_mph, label)
    """
    if raw_value is None:
        return "", "no wind data available"

    # NWS uses WMO unit codes
    if "m_s" in unit_code:
        # Meters per second to MPH
        speed_mph = round(raw_value * 2.23694)
        return speed_mph, "mph"
    elif "km_h" in unit_code:
        # Kilometers per hour to MPH
        speed_mph = round(raw_value * 0.621371)
        return speed_mph, "mph"
    else:
        # Fallback for unexpected units
        return "", "no wind data available"


def degrees_to_cardinal(degrees):
    """Convert wind direction in degrees to cardinal direction"""
    if degrees is None:
        return ""

    directions = [
        'N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE',
        'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'
    ]
    idx = int((degrees + 11.25) / 22.5)
    return directions[idx % 16]


def get_moon_details(moon_phase):
    """
    Map the 0-28 moon phase number to a name and emoji

    Returns: tuple (name, emoji)
    """
    if moon_phase < 1.84:
        return ("New Moon", "🌑")
    elif moon_phase < 5.53:
        return ("Waxing Crescent", "🌒")
    elif moon_phase < 9.22:
        return ("First Quarter", "🌓")
    elif moon_phase < 12.91:
        return ("Waxing Gibbous", "🌔")
    elif moon_phase < 16.61:
        return ("Full Moon", "🌕")
    elif moon_phase < 20.30:
        return ("Waning Gibbous", "🌖")
    elif moon_phase < 23.99:
        return ("Last Quarter", "🌗")
    else:
        return ("Waning Crescent", "🌘")


def get_astronomy_data(lat, lon):
    """
    Calculate sunrise, sunset, moonrise, moonset, moon phase, and enhanced moon data
    Uses Skyfield (NASA JPL data) for accurate moonrise/moonset times

    Returns: dict with astronomy data including raw datetime objects
    """
    # Find timezone for the location
    tf = TimezoneFinder()
    tz_name = tf.timezone_at(lng=lon, lat=lat)
    local_tz = pytz.timezone(tz_name)
    
    # Get current time in local timezone
    current_time = datetime.now(local_tz)

    # Create location for Astral (still using for sun and moon phase)
    city = LocationInfo("", "", tz_name, lat, lon)

    # Sun calculations (Astral is fine for sun)
    # At extreme latitudes the sun may never cross the 6° depression threshold,
    # causing Astral to raise ValueError (midnight sun / polar night).
    try:
        sun_data = sun(city.observer, date=date.today(), tzinfo=local_tz)
        sunrise_str = sun_data['sunrise'].strftime('%-I:%M %p')
        sunset_str = sun_data['sunset'].strftime('%-I:%M %p')
    except ValueError:
        sun_data = {}
        sunrise_str = "—"
        sunset_str = "—"

    # Moon phase (still using Astral)
    moon_phase = astral_phase(date.today())
    moon_name, moon_emoji = get_moon_details(moon_phase)
    
    # Moon illumination percentage (0-100%)
    moon_illumination = round((1 - abs(moon_phase - 14) / 14) * 100)

    # Meeus Ch. 15 moonrise / moonset — today, yesterday, tomorrow
    today = date.today()
    yesterday = today - timedelta(days=1)
    tomorrow  = today + timedelta(days=1)

    def _moon_day(d):
        try:
            rise, set_, vis = _calc_moonrise_moonset(lat, lon, d, local_tz)
            return {
                'rise':    rise,
                'set':     set_,
                'rise_str': rise.strftime('%-I:%M %p') if rise else "—",
                'set_str':  set_.strftime('%-I:%M %p')  if set_  else "—",
                'visible':  vis,
            }
        except Exception as e:
            print(f"Meeus moonrise error ({d}): {e}")
            return {'rise': None, 'set': None, 'rise_str': "—", 'set_str': "—", 'visible': False}

    moon_yesterday = _moon_day(yesterday)
    moon_today     = _moon_day(today)
    moon_tomorrow  = _moon_day(tomorrow)

    m_rise       = moon_today['rise']
    m_set        = moon_today['set']
    moonrise_str = moon_today['rise_str']
    moonset_str  = moon_today['set_str']
    moon_visible = moon_today['visible']

    # Collect all events from the 3-day window and sort by actual datetime.
    # Each algorithm can produce events on adjacent local days (m slightly
    # outside [0,1]), so we bucket by real local date rather than by which
    # day's algorithm produced the event.
    def _evt(day, kind):
        t = day['rise'] if kind == 'rise' else day['set']
        if t is None:
            return None
        return {
            'time':     t,
            'time_str': t.strftime('%-I:%M %p'),
            'label':    'Moonrise' if kind == 'rise' else 'Moonset',
            'emoji':    '🌕'       if kind == 'rise' else '🌑',
        }

    _all = [e for e in [
        _evt(moon_yesterday, 'rise'), _evt(moon_yesterday, 'set'),
        _evt(moon_today,     'rise'), _evt(moon_today,     'set'),
        _evt(moon_tomorrow,  'rise'), _evt(moon_tomorrow,  'set'),
    ] if e is not None]
    _all.sort(key=lambda e: e['time'])

    # Deduplicate: same event can be computed by two adjacent day algorithms
    _deduped = []
    for evt in _all:
        if _deduped:
            gap = abs((evt['time'] - _deduped[-1]['time']).total_seconds())
            if gap < 1800 and evt['label'] == _deduped[-1]['label']:
                continue
        _deduped.append(evt)

    _today_start     = local_tz.localize(datetime(today.year, today.month, today.day))
    _today_end       = _today_start + timedelta(days=1)
    _yesterday_start = _today_start - timedelta(days=1)
    _tomorrow_end    = _today_end   + timedelta(days=1)

    moon_today_events     = [e for e in _deduped if _today_start     <= e['time'] < _today_end]
    _yesterday_evts       = [e for e in _deduped if _yesterday_start <= e['time'] < _today_start]
    _tomorrow_evts        = [e for e in _deduped if _today_end       <= e['time'] < _tomorrow_end]

    moon_yesterday_highlight = _yesterday_evts[-1] if _yesterday_evts else None
    moon_tomorrow_highlight  = _tomorrow_evts[0]   if _tomorrow_evts  else None

    _now_local = datetime.now(local_tz)
    _future = [e for e in _deduped if e['time'] > _now_local]
    moon_next_event = _future[0] if _future else None

    # Find next full moon and new moon
    next_full_moon_str = ""
    next_new_moon_str = ""

    try:
        for i in range(1, 40):
            check_date = today + timedelta(days=i)
            moon_phase_check = astral_phase(check_date)
            
            # Full moon is around day 14 (within 1 day for better detection)
            if not next_full_moon_str and abs(moon_phase_check - 14) < 1.0:
                days_until = i
                if days_until == 1:
                    next_full_moon_str = "tomorrow"
                else:
                    next_full_moon_str = f"in {days_until} days"
            
            # New moon is around day 0 or 28 (within 1 day)
            if not next_new_moon_str and (moon_phase_check < 1.0 or moon_phase_check > 27.0):
                days_until = i
                if days_until == 1:
                    next_new_moon_str = "tomorrow"
                else:
                    next_new_moon_str = f"in {days_until} days"
            
            # Stop if we found both
            if next_full_moon_str and next_new_moon_str:
                break
        
        # Fallback if not found (shouldn't happen but just in case)
        if not next_full_moon_str:
            next_full_moon_str = "N/A"
        if not next_new_moon_str:
            next_new_moon_str = "N/A"
            
    except Exception as e:
        next_full_moon_str = "N/A"
        next_new_moon_str = "N/A"

    return {
        "sunrise": sunrise_str,
        "sunset": sunset_str,
        "moon_name": moon_name,
        "moon_emoji": moon_emoji,
        "moon_illumination": moon_illumination,
        "moon_visible": moon_visible,
        "moonrise": moonrise_str,
        "moonset": moonset_str,
        "moonrise_dt": m_rise,
        "moonset_dt": m_set,
        "moon_yesterday": moon_yesterday,
        "moon_today": moon_today,
        "moon_tomorrow": moon_tomorrow,
        "moon_yesterday_highlight": moon_yesterday_highlight,
        "moon_tomorrow_highlight": moon_tomorrow_highlight,
        "moon_next_event": moon_next_event,
        "moon_today_events": moon_today_events,
        "next_full_moon": next_full_moon_str,
        "next_new_moon": next_new_moon_str,
        "sunrise_dt": sun_data.get('sunrise'),
        "sunset_dt": sun_data.get('sunset'),
        "timezone": tz_name
    }