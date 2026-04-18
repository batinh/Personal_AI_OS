import os
import requests

from app.core.logging_conf import get_module_logger

logger = get_module_logger("weather")

def get_today_weather():
    """
    Fetch current weather and today's forecast from OpenWeatherMap.
    Zone 1: Logic and Logging in English.
    """
    api_key = os.getenv("OPENWEATHER_API_KEY")
    lat = os.getenv("USER_LAT", "10.7626")
    lon = os.getenv("USER_LON", "106.6601")
    
    if not api_key:
        logger.error("[WEATHER] Missing OPENWEATHER_API_KEY in environment.")
        return "Weather data unavailable (Missing API Key)."

    try:
        # Fetching current weather in metric units
        url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={api_key}&units=metric&lang=vi"
        response = requests.get(url, timeout=7)
        data = response.json()

        if response.status_code == 200:
            temp = data['main']['temp']
            humidity = data['main']['humidity']
            description = data['weather'][0]['description']
            wind = data['wind']['speed']
            
            report = f"Nhiệt độ: {temp}°C, Độ ẩm: {humidity}%, Trạng thái: {description}, Gió: {wind}m/s"
            logger.info(f"[WEATHER] Successfully fetched: {report}")
            return report
        else:
            logger.warning(f"[WEATHER] API Error: {data.get('message')}")
            return "Weather data unavailable (API Error)."
    except Exception as e:
        logger.error(f"[WEATHER] Connection error: {e}")
        return "Weather data unavailable (Connection Error)."