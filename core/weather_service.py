"""
Weather service module - handles all external API calls and data processing
"""
import requests
from geopy.geocoders import Nominatim
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
        geolocator = Nominatim(user_agent="my_weather_app")
        
        try:
            location = geolocator.geocode(f"{address}, USA", timeout=timeout)
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
