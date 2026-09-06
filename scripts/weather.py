#!/usr/bin/env python3
"""Today's weather for the home page. Open-Meteo by default (no key); Yandex Weather when YANDEX_WEATHER_KEY is set.
Location from env WEATHER_LAT / WEATHER_LON / WEATHER_CITY (defaults: Moscow). Cached for 20 minutes."""
import json, os, time, urllib.parse, urllib.request

LAT = float(os.environ.get("WEATHER_LAT", "55.7558"))
LON = float(os.environ.get("WEATHER_LON", "37.6173"))
CITY = os.environ.get("WEATHER_CITY", "Москва")
TZ = os.environ.get("WEATHER_TZ", "Europe/Moscow")
_cache = {"t": 0, "data": None}

# WMO weather codes → (russian text, emoji)
WMO = {0: ("Ясно", "☀️"), 1: ("В основном ясно", "🌤"), 2: ("Переменная облачность", "⛅"), 3: ("Пасмурно", "☁️"),
       45: ("Туман", "🌫"), 48: ("Изморозь", "🌫"), 51: ("Лёгкая морось", "🌦"), 53: ("Морось", "🌦"), 55: ("Сильная морось", "🌧"),
       56: ("Ледяная морось", "🌧"), 57: ("Ледяная морось", "🌧"), 61: ("Небольшой дождь", "🌦"), 63: ("Дождь", "🌧"), 65: ("Сильный дождь", "🌧"),
       66: ("Ледяной дождь", "🌧"), 67: ("Ледяной дождь", "🌧"), 71: ("Небольшой снег", "🌨"), 73: ("Снег", "🌨"), 75: ("Сильный снег", "❄️"),
       77: ("Снежная крупа", "🌨"), 80: ("Ливень", "🌦"), 81: ("Ливень", "🌧"), 82: ("Сильный ливень", "⛈"), 85: ("Снегопад", "🌨"),
       86: ("Сильный снегопад", "❄️"), 95: ("Гроза", "⛈"), 96: ("Гроза с градом", "⛈"), 99: ("Гроза с градом", "⛈")}
YA = {"clear": ("Ясно", "☀️"), "partly-cloudy": ("Малооблачно", "🌤"), "cloudy": ("Облачно с прояснениями", "⛅"), "overcast": ("Пасмурно", "☁️"),
      "light-rain": ("Небольшой дождь", "🌦"), "rain": ("Дождь", "🌧"), "heavy-rain": ("Сильный дождь", "🌧"), "showers": ("Ливень", "🌧"),
      "wet-snow": ("Дождь со снегом", "🌨"), "light-snow": ("Небольшой снег", "🌨"), "snow": ("Снег", "🌨"), "snow-showers": ("Снегопад", "❄️"),
      "hail": ("Град", "🌨"), "thunderstorm": ("Гроза", "⛈"), "thunderstorm-with-rain": ("Дождь с грозой", "⛈"), "thunderstorm-with-hail": ("Гроза с градом", "⛈")}


def get_json(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": "movies-site/1.0", **(headers or {})})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def open_meteo():
    q = urllib.parse.urlencode({
        "latitude": LAT, "longitude": LON, "timezone": TZ, "forecast_days": 2,
        "current": "temperature_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m,is_day",
        "hourly": "temperature_2m,precipitation_probability,precipitation,weather_code",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,sunrise,sunset,weather_code"})
    d = get_json("https://api.open-meteo.com/v1/forecast?" + q)
    c, h, dy = d["current"], d["hourly"], d["daily"]
    now_hour = c["time"][:13]
    idx = next((i for i, t in enumerate(h["time"]) if t[:13] == now_hour), 0)
    today = c["time"][:10]
    hours = []
    for i in range(idx, min(idx + 24, len(h["time"]))):
        code = h["weather_code"][i]
        hours.append({"time": h["time"][i][11:16], "day": h["time"][i][:10] == today, "temp": round(h["temperature_2m"][i]),
                      "precipProb": h["precipitation_probability"][i], "precip": h["precipitation"][i], "icon": WMO.get(code, ("", "•"))[1]})
    code = c["weather_code"]
    return {
        "city": CITY, "source": "Open-Meteo", "updated": c["time"],
        "now": {"temp": round(c["temperature_2m"]), "feels": round(c["apparent_temperature"]), "condition": WMO.get(code, ("", ""))[0],
                "icon": WMO.get(code, ("", "🌡"))[1], "precip": c["precipitation"], "wind": round(c["wind_speed_10m"] / 3.6, 1), "isDay": bool(c["is_day"])},
        "today": {"tmin": round(dy["temperature_2m_min"][0]), "tmax": round(dy["temperature_2m_max"][0]),
                  "precipSum": dy["precipitation_sum"][0], "precipProbMax": dy["precipitation_probability_max"][0],
                  "condition": WMO.get(dy["weather_code"][0], ("", ""))[0], "icon": WMO.get(dy["weather_code"][0], ("", ""))[1],
                  "sunrise": dy["sunrise"][0][11:16], "sunset": dy["sunset"][0][11:16]},
        "hours": hours,
    }


def yandex(key):
    d = get_json(f"https://api.weather.yandex.ru/v2/forecast?lat={LAT}&lon={LON}&lang=ru_RU&limit=2&hours=true&extra=true",
                 {"X-Yandex-Weather-Key": key})
    f, day = d["fact"], d["forecasts"][0]
    hours = []
    all_hours = [(0, hh) for hh in day.get("hours", [])] + [(1, hh) for hh in d["forecasts"][1].get("hours", [])]
    now_h = int(time.strftime("%H", time.localtime(d.get("now", time.time()))))
    for di, hh in all_hours:
        if di == 0 and int(hh["hour"]) < now_h:
            continue
        if len(hours) >= 24:
            break
        hours.append({"time": f"{int(hh['hour']):02d}:00", "day": di == 0, "temp": hh["temp"], "precipProb": hh.get("prec_prob"),
                      "precip": hh.get("prec_mm"), "icon": YA.get(hh.get("condition"), ("", "•"))[1]})
    parts = day["parts"]
    tmin = min(p["temp_min"] for p in parts.values() if isinstance(p, dict) and "temp_min" in p)
    tmax = max(p["temp_max"] for p in parts.values() if isinstance(p, dict) and "temp_max" in p)
    dp = parts.get("day", {})
    return {
        "city": CITY, "source": "Яндекс Погода", "updated": time.strftime("%Y-%m-%dT%H:%M", time.localtime(d.get("now", time.time()))),
        "now": {"temp": f["temp"], "feels": f["feels_like"], "condition": YA.get(f["condition"], (f["condition"], ""))[0],
                "icon": YA.get(f["condition"], ("", "🌡"))[1], "precip": f.get("prec_strength", 0), "wind": f.get("wind_speed"), "isDay": f.get("daytime") == "d"},
        "today": {"tmin": tmin, "tmax": tmax, "precipSum": sum(p.get("prec_mm", 0) for p in parts.values() if isinstance(p, dict)),
                  "precipProbMax": max(p.get("prec_prob", 0) for p in parts.values() if isinstance(p, dict)),
                  "condition": YA.get(dp.get("condition"), ("", ""))[0], "icon": YA.get(dp.get("condition"), ("", ""))[1],
                  "sunrise": day.get("sunrise"), "sunset": day.get("sunset")},
        "hours": hours,
    }


def today():
    if _cache["data"] and time.time() - _cache["t"] < 1200:
        return _cache["data"]
    key = os.environ.get("YANDEX_WEATHER_KEY")
    data = yandex(key) if key else open_meteo()
    _cache.update(t=time.time(), data=data)
    return data


if __name__ == "__main__":
    print(json.dumps(today(), ensure_ascii=False, indent=1))
