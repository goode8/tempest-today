"""
ADD THIS TO views.py
===================
1. Add this import at the top (it's already there, but double-check):
   from collections import defaultdict

2. Paste the two functions below at the bottom of views.py
   (after get_current_weather)
"""

from collections import defaultdict


def build_7day_compact(forecasts):
    """
    Collapse NWS period-based forecasts (daytime/nighttime alternating) into
    7 clean day-summary dicts for the compact view.

    NWS returns periods like:
      [Tonight, Monday, Monday Night, Tuesday, Tuesday Night, ...]
    or starting with a day period:
      [This Afternoon, Tonight, Monday, Monday Night, ...]

    We want one row per calendar day with:
      - day label (Mon, Tue, etc.)
      - high temp  (daytime period)
      - low temp   (nighttime period)
      - max rain % across both periods
      - umbrella flag (rain % >= 30)
      - short forecast text (daytime preferred)
    """
    days = {}   # key = day label like "Monday", value = dict
    order = []  # preserve insertion order (Python 3.7+ dicts keep order, but let's be safe)

    for period in forecasts:
        name = period.get("name", "")
        is_day = period.get("isDaytime", True)
        temp = period.get("temperature")
        precip = None
        precip_raw = period.get("probabilityOfPrecipitation", {})
        if isinstance(precip_raw, dict):
            precip = precip_raw.get("value")

        short = period.get("shortForecast", "")

        # Normalize the day key: strip "Night" / "This " / "Late " etc.
        # so "Monday Night" -> "Monday", "This Afternoon" -> "Today"
        if "Tonight" in name or "Night" in name:
            if "Tonight" in name:
                day_key = "Tonight→Tomorrow"  # special case handled below
                # We'll use "Today" as the bucket for Tonight
                day_key = "Today"
            else:
                # "Monday Night" -> "Monday"
                day_key = name.replace(" Night", "").replace(" night", "").strip()
        elif name.startswith("This ") or name.startswith("Late "):
            day_key = "Today"
        elif name == "Today":
            day_key = "Today"
        else:
            day_key = name  # "Monday", "Tuesday", etc.

        if day_key not in days:
            days[day_key] = {
                "label": day_key,
                "high": None,
                "low": None,
                "max_precip": None,
                "short_forecast": "",
            }
            order.append(day_key)

        entry = days[day_key]

        # Assign high/low
        if is_day:
            if entry["high"] is None:
                entry["high"] = temp
            if not entry["short_forecast"]:
                entry["short_forecast"] = short
        else:
            if entry["low"] is None:
                entry["low"] = temp

        # Track max precipitation probability
        if precip is not None:
            try:
                precip_int = int(precip)
                if entry["max_precip"] is None or precip_int > entry["max_precip"]:
                    entry["max_precip"] = precip_int
            except (ValueError, TypeError):
                pass

    # Build final list: skip "Today" (user said skip current/tonight),
    # take up to 7 future days
    result = []
    for key in order:
        if key == "Today":
            continue  # skip current conditions as requested
        entry = days[key]
        precip_val = entry["max_precip"]
        result.append({
            "label": entry["label"],
            "high": entry["high"],
            "low": entry["low"],
            "precip": precip_val,
            "umbrella": precip_val is not None and precip_val >= 30,
            "short_forecast": entry["short_forecast"],
        })
        if len(result) >= 7:
            break

    return result


def compact_forecast(request):
    """
    Returns the compact 7-day HTML partial.
    Expects: POST with 'address' and optionally 'unit'
    Used by the Quick Look button via fetch().
    """
    from django.http import HttpResponseBadRequest

    if request.method != "POST":
        return HttpResponseBadRequest("POST required")

    address = request.POST.get("address", "").strip()
    unit = request.POST.get("unit", "F")

    if not address:
        from django.template.loader import render_to_string
        from django.http import HttpResponse
        html = render_to_string("core/_compact_forecast.html", {
            "error": "No address provided.",
            "unit": unit,
        }, request=request)
        return HttpResponse(html)

    weather_service = WeatherService()

    # Try cache first
    from django.core.cache import cache
    geocode_cache_key = f"geocode_{address.lower().replace(' ', '_')}"
    cached_geocode = cache.get(geocode_cache_key)

    if cached_geocode:
        lat = cached_geocode['lat']
        lon = cached_geocode['lon']
        location = cached_geocode['location']
    else:
        lat, lon, location = weather_service.get_location_coordinates(address)
        if location:
            cache.set(geocode_cache_key, {'lat': lat, 'lon': lon, 'location': location}, 2592000)

    if not location:
        from django.template.loader import render_to_string
        from django.http import HttpResponse
        html = render_to_string("core/_compact_forecast.html", {
            "error": "Location not found.",
            "unit": unit,
        }, request=request)
        return HttpResponse(html)

    # Check weather cache
    weather_cache_key = f"weather_{round(lat, 2)}_{round(lon, 2)}"
    cached_weather = cache.get(weather_cache_key)

    if cached_weather:
        raw_forecasts = cached_weather['forecasts']
    else:
        metadata = weather_service.get_metadata(lat, lon)
        if not metadata:
            from django.template.loader import render_to_string
            from django.http import HttpResponse
            html = render_to_string("core/_compact_forecast.html", {
                "error": "Weather data unavailable.",
                "unit": unit,
            }, request=request)
            return HttpResponse(html)
        raw_forecasts = weather_service.get_forecast(metadata["forecast"])

    # Convert temps if Celsius requested
    if unit == 'C':
        for period in raw_forecasts:
            period['temperature'] = convert_temperature(
                period['temperature'], from_unit='F', to_unit='C'
            )

    days = build_7day_compact(raw_forecasts)

    # Umbrella anywhere this week?
    any_umbrella = any(d["umbrella"] for d in days)

    from django.template.loader import render_to_string
    from django.http import HttpResponse
    html = render_to_string("core/_compact_forecast.html", {
        "days": days,
        "address": address,
        "unit": unit,
        "any_umbrella": any_umbrella,
    }, request=request)
    return HttpResponse(html)
