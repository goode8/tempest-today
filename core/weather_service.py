"""
Weather service module - handles all external API calls and data processing
"""
import requests
import os
from geopy.geocoders import Nominatim, Photon
from geopy.exc import GeocoderTimedOut, GeocoderServiceError


class LocationIQResult:
    """Simple location object to match Geopy's interface"""
    def __init__(self, lat, lon, display_name, address_data):
        self.latitude = lat
        self.longitude = lon
        self.address = display_name
        self.raw = {'address': address_data}


class WeatherService:
    """Handles all National Weather Service API interactions"""
    
    def __init__(self, locationiq_api_key=None):
        self.headers = {"User-Agent": "tempesttoday.pythonanywhere.com (tempesttoday@gmail.com)"}
        self.base_url = "https://api.weather.gov"
        self.locationiq_api_key = locationiq_api_key or os.getenv('LOCATIONIQ_API_KEY')
    
    def get_location_coordinates(self, address, timeout=10):
        """
        Convert address to coordinates using geocoding
        Priority: LocationIQ (primary) -> Nominatim (fallback) -> Photon (fallback 2)
        
        Returns: tuple (lat, lon, location_obj) or (None, None, None) if failed
        """
        import re
        
        # Check if it looks like a ZIP code (5 digits or 5+4 format)
        zip_pattern = re.match(r'^\d{5}(-\d{4})?$', address.strip())
        is_zip = bool(zip_pattern)
        
        if is_zip:
            zip_code = address.strip()
            zip_num = int(zip_code[:5])
            
            if zip_num < 501 or zip_num > 99950:
                return None, None, None
        
        # === PRIMARY: Try LocationIQ first (if API key available) ===
        if self.locationiq_api_key:
            try:
                # Don't append USA if user specified another country
                international_keywords = ['canada', 'england', 'uk', 'mexico', 'france', 'germany', 'japan', 'china', 'australia']
                query = address
                if not any(keyword in address.lower() for keyword in international_keywords):
                    query = f"{address}, USA"
                
                url = "https://us1.locationiq.com/v1/search"
                params = {
                    'key': self.locationiq_api_key,
                    'q': query,
                    'format': 'json',
                    'limit': 1,
                    'addressdetails': 1,
                }
                
                # Only restrict to US if no international keyword
                if query.endswith(', USA'):
                    params['countrycodes'] = 'us'
                
                response = requests.get(url, params=params, timeout=timeout)
                
                if response.status_code == 200:
                    data = response.json()
                    if data:
                        result = data[0]
                        lat = float(result['lat'])
                        lon = float(result['lon'])
                        
                        # Parse address components first
                        address_parts = {
                            'country_code': result.get('address', {}).get('country_code', ''),
                            'state': result.get('address', {}).get('state'),
                            'ISO3166-2-lvl4': f"US-{result.get('address', {}).get('state')}" if result.get('address', {}).get('state') else None,
                            'country': result.get('address', {}).get('country', 'United States')
                        }
                        
                        location = LocationIQResult(lat, lon, result.get('display_name'), address_parts)
                        
                        # Verify US bounds (including Alaska & Hawaii)
                        if 18 <= lat <= 72 and -180 <= lon <= -65:
                            print(f"✓ LocationIQ: {address} -> {lat}, {lon}")
                            return lat, lon, location
                        else:
                            # Outside US - return location so views.py can show international error
                            print(f"✓ LocationIQ (non-US): {address} -> {lat}, {lon} ({address_parts.get('country')})")
                            return lat, lon, location
                
                # If LocationIQ fails, fall through to Nominatim
                print(f"LocationIQ failed (status {response.status_code}), trying Nominatim...")
                
            except Exception as e:
                print(f"LocationIQ error: {e}, trying Nominatim...")
        
        # === FALLBACK 1: Try Nominatim ===
        try:
            nom = Nominatim(user_agent="my_weather_app")
            
            if is_zip:
                # ZIP code specific logic
                location = nom.geocode(
                    query={'postalcode': address.strip(), 'country': 'us'},
                    timeout=timeout,
                    exactly_one=True,
                    addressdetails=True
                )
                
                if not location:
                    location = nom.geocode(
                        f"{address}, United States",
                        timeout=timeout,
                        exactly_one=True,
                        addressdetails=True,
                        country_codes='us'
                    )
            else:
                # City, State logic with state abbreviation parsing
                parts = re.split(r'[,\s]+', address.strip())
                state_abbrevs = {
                    'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA',
                    'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD',
                    'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ',
                    'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC',
                    'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY', 'DC'
                }
                
                if len(parts) >= 2 and parts[-1].upper() in state_abbrevs:
                    city = ' '.join(parts[:-1])
                    state = parts[-1].upper()
                    location = nom.geocode(
                        query={'city': city, 'state': state, 'country': 'us'},
                        timeout=timeout,
                        exactly_one=True,
                        addressdetails=True
                    )
                else:
                    location = nom.geocode(
                        f"{address}, USA",
                        timeout=timeout,
                        exactly_one=True,
                        addressdetails=True,
                        country_codes='us'
                    )
            
            if location:
                # Verify US bounds
                if 18 <= location.latitude <= 72 and -180 <= location.longitude <= -65:
                    print(f"✓ Nominatim: {address} -> {location.latitude}, {location.longitude}")
                    return location.latitude, location.longitude, location
        
        except (GeocoderTimedOut, GeocoderServiceError) as e:
            print(f"Nominatim error: {e}, trying Photon...")
        except Exception as e:
            print(f"Nominatim error: {e}, trying Photon...")
        
        # === FALLBACK 2: Try Photon (only for non-ZIP, as it doesn't handle ZIPs well) ===
        if not is_zip:
            try:
                photon = Photon(user_agent="my_weather_app")
                location = photon.geocode(f"{address}, USA", timeout=timeout)
                
                if location and 18 <= location.latitude <= 72 and -180 <= location.longitude <= -65:
                    print(f"✓ Photon: {address} -> {location.latitude}, {location.longitude}")
                    return location.latitude, location.longitude, location
            
            except Exception as e:
                print(f"Photon error: {e}")
        
        # All geocoders failed
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
