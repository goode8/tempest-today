"""
Utility functions for weather data processing and astronomy calculations
"""
from astral import LocationInfo
from astral.sun import sun
from astral.moon import moonrise, moonset, phase
from datetime import date
from timezonefinder import TimezoneFinder
import pytz


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
    Calculate sunrise, sunset, moonrise, moonset, and moon phase

    Returns: dict with astronomy data including raw datetime objects
    """
    # Find timezone for the location
    tf = TimezoneFinder()
    tz_name = tf.timezone_at(lng=lon, lat=lat)
    local_tz = pytz.timezone(tz_name)

    # Create location for Astral
    city = LocationInfo("", "", tz_name, lat, lon)

    # Sun calculations
    sun_data = sun(city.observer, date=date.today(), tzinfo=local_tz)
    sunrise_str = sun_data['sunrise'].strftime('%-I:%M %p')
    sunset_str = sun_data['sunset'].strftime('%-I:%M %p')

    # Moon phase
    moon_phase = phase(date.today())
    moon_name, moon_emoji = get_moon_details(moon_phase)

    # Moon rise/set
    try:
        m_rise = moonrise(city.observer, date=date.today(), tzinfo=local_tz)
        m_set = moonset(city.observer, date=date.today(), tzinfo=local_tz)

        moonrise_str = m_rise.strftime('%-I:%M %p') if m_rise else "No rise today"
        moonset_str = m_set.strftime('%-I:%M %p') if m_set else "No set today"
    except ValueError:
        moonrise_str, moonset_str = "N/A", "N/A"

    return {
        "sunrise": sunrise_str,
        "sunset": sunset_str,
        "moonrise": moonrise_str,
        "moonset": moonset_str,
        "moon_name": moon_name,
        "moon_emoji": moon_emoji,
        "sunrise_dt": sun_data['sunrise'],  # Raw datetime for comparison
        "sunset_dt": sun_data['sunset'],    # Raw datetime for comparison
        "timezone": tz_name
    }