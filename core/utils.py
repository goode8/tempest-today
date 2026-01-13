"""
Utility functions for weather data processing and astronomy calculations
"""
from astral import LocationInfo
from astral.sun import sun
from astral.moon import phase as astral_phase
from datetime import date, datetime, timedelta
from timezonefinder import TimezoneFinder
import pytz

# Skyfield for accurate moon rise/set
# from skyfield.api import load, wgs84
# from skyfield import almanac


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
    sun_data = sun(city.observer, date=date.today(), tzinfo=local_tz)
    sunrise_str = sun_data['sunrise'].strftime('%-I:%M %p')
    sunset_str = sun_data['sunset'].strftime('%-I:%M %p')

    # Moon phase (still using Astral)
    moon_phase = astral_phase(date.today())
    moon_name, moon_emoji = get_moon_details(moon_phase)
    
    # Moon illumination percentage (0-100%)
    moon_illumination = round((1 - abs(moon_phase - 14) / 14) * 100)

    # === SKYFIELD for accurate moonrise/moonset ===
    # TEMPORARILY DISABLED - accuracy issues need investigation
    # m_rise = None
    # m_set = None
    # moonrise_str = "N/A"
    # moonset_str = "N/A"
    
    # try:
    #     # Load Skyfield ephemeris data
    #     eph = load('de421.bsp')  # JPL ephemeris
    #     earth = eph['earth']
    #     moon = eph['moon']
    #     
    #     # Create observer location
    #     observer = earth + wgs84.latlon(lat, lon)
    #     
    #     # Get timescale
    #     ts = load.timescale()
    #     
    #     # Search for moonrise/moonset today
    #     t0 = ts.from_datetime(datetime.combine(date.today(), datetime.min.time()).replace(tzinfo=local_tz))
    #     t1 = ts.from_datetime(datetime.combine(date.today(), datetime.max.time()).replace(tzinfo=local_tz))
    #     
    #     # Find rise/set events
    #     t, y = almanac.find_risings(observer, moon, t0, t1)
    #     
    #     # Parse results - y values: True = rise, False = set
    #     for time, is_rise in zip(t, y):
    #         dt = time.astimezone(local_tz)
    #         if is_rise and not m_rise:
    #             m_rise = dt
    #             moonrise_str = dt.strftime('%-I:%M %p')
    #         elif not is_rise and not m_set:
    #             m_set = dt
    #             moonset_str = dt.strftime('%-I:%M %p')
    #     
    #     # If no rise or set found today
    #     if not m_rise:
    #         moonrise_str = "No rise today"
    #     if not m_set:
    #         moonset_str = "No set today"
    #         
    # except Exception as e:
    #     # Fallback to "N/A" if Skyfield fails
    #     print(f"Skyfield error: {e}")
    #     moonrise_str, moonset_str = "N/A", "N/A"
    
    # Calculate moon transit (highest point in sky) - best viewing time
    # moon_transit_time = None
    # moon_transit_str = "N/A"
    # if m_rise and m_set:
    #     # Moon transit is approximately halfway between rise and set
    #     time_diff = m_set - m_rise
    #     moon_transit_time = m_rise + (time_diff / 2)
    #     moon_transit_str = moon_transit_time.strftime('%-I:%M %p') + " (highest in sky)"
    
    # Determine if moon is currently visible
    # moon_visible = False
    # if m_rise and m_set:
    #     if m_rise < m_set:
    #         # Normal case: moon rises then sets in same day
    #         moon_visible = m_rise <= current_time <= m_set
    #     else:
    #         # Moon set before rise (crosses midnight)
    #         moon_visible = current_time >= m_rise or current_time <= m_set
    
    # Find next full moon and new moon
    next_full_moon_str = ""
    next_new_moon_str = ""
    
    try:
        # Check next 40 days for full and new moons
        today = date.today()
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
        # "moonrise": moonrise_str,
        # "moonset": moonset_str,
        "moon_name": moon_name,
        "moon_emoji": moon_emoji,
        "moon_illumination": moon_illumination,
        # "moon_transit": moon_transit_str,
        # "moon_visible": moon_visible,
        "next_full_moon": next_full_moon_str,
        "next_new_moon": next_new_moon_str,
        "sunrise_dt": sun_data['sunrise'],  # Raw datetime for comparison
        "sunset_dt": sun_data['sunset'],    # Raw datetime for comparison
        # "moonrise_dt": m_rise,  # Raw datetime for moon visibility logic
        # "moonset_dt": m_set,    # Raw datetime for moon visibility logic
        "timezone": tz_name
    }