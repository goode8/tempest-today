"""
Weather service module - handles all external API calls and data processing
"""
import requests
from geopy.geocoders import Nominatim, Photon
from geopy.exc import GeocoderTimedOut, GeocoderServiceError


class WeatherService:
    """Handles all National Weather Service API interactions"""
    
    def __init__(self):
        self.headers = {"User-Agent": "tempesttoday.pythonanywhere.com (tempesttoday@gmail.com)"}
        self.base_url = "https://api.weather.gov"
    
    def get_location_coordinates(self, address, timeout=10):
        """
        Convert address to coordinates using geocoding
        
        Returns: tuple (lat, lon, location_obj) or (None, None, None) if failed
        """
        import re
        
        # Check if it looks like a ZIP code (5 digits or 5+4 format)
        zip_pattern = re.match(r'^\d{5}(-\d{4})?$', address.strip())
        
        if zip_pattern:
            # It's a ZIP code - use Nominatim with explicit postalcode parameter
            zip_code = address.strip()
            
            # Validate it's a real US ZIP code range (00501-99950)
            zip_num = int(zip_code[:5])
            
            if zip_num < 501 or zip_num > 99950:
                return None, None, None
            
            # Use Nominatim with structured query (more reliable for ZIP codes)
            try:
                nom = Nominatim(user_agent="my_weather_app")
                
                # Try structured query first (most reliable)
                location = nom.geocode(
                    query={'postalcode': zip_code, 'country': 'us'},
                    timeout=timeout,
                    exactly_one=True,
                    addressdetails=True
                )
                
                # If structured query fails, try simple query as fallback
                if not location:
                    location = nom.geocode(
                        f"{zip_code}, United States",
                        timeout=timeout,
                        exactly_one=True,
                        addressdetails=True,
                        country_codes='us'
                    )
                
                if location:
                    # Verify it's actually in the US (lat 24-50, lon -125 to -65)
                    if 24 <= location.latitude <= 50 and -125 <= location.longitude <= -65:
                        return location.latitude, location.longitude, location
                
                return None, None, None
                
            except Exception as e:
                print(f"Geocoding error for ZIP {zip_code}: {e}")
                return None, None, None
        
        else:
            # City, State - use Nominatim
            geolocator = Nominatim(user_agent="my_weather_app")
            search_query = f"{address}, USA"
            
            try:
                location = geolocator.geocode(
                    search_query, 
                    timeout=timeout, 
                    exactly_one=True, 
                    addressdetails=True, 
                    country_codes='us'
                )
                
                if location:
                    return location.latitude, location.longitude, location
                
                return None, None, None
                
            except (GeocoderTimedOut, GeocoderServiceError):
                raise Exception("Geocoding service timeout - please try again")
    
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
        
        Returns: tuple (station_id, station_name) or (None, None)
        """
        response = requests.get(stations_url, headers=self.headers)
        data = response.json()
        
        features = data.get("features", [])
        if features:
            props = features[0]["properties"]
            return props.get("stationIdentifier"), props.get("name")
        
        return None, None
    
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
