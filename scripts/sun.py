#!/usr/bin/env python3
"""Восход и закат на сегодня — без сети и без ключей.

Погода (`weather.py`) знает закат, но берёт его из Open-Meteo, а свет в квартире не должен зависеть от
того, дотянулся ли mini до интернета: в вечер, когда лёг VPN, лампы всё равно обязаны зажечься вовремя.
Поэтому здесь обычная астрономия (NOAA sunrise equation, точность около минуты) на голом stdlib.

  python3 scripts/sun.py [YYYY-MM-DD]
"""
import datetime as dt
import math
import os
import sys
from zoneinfo import ZoneInfo

LAT = float(os.environ.get("WEATHER_LAT", "55.7558"))
LON = float(os.environ.get("WEATHER_LON", "37.6173"))
TZ = os.environ.get("WEATHER_TZ", "Europe/Moscow")
ZENITH = -0.833          # верхний край диска у горизонта с поправкой на рефракцию


def tz():
    return ZoneInfo(TZ)


def _julian(date):
    a = (14 - date.month) // 12
    y, m = date.year + 4800 - a, date.month + 12 * a - 3
    jdn = date.day + (153 * m + 2) // 5 + 365 * y + y // 4 - y // 100 + y // 400 - 32045
    return jdn - 0.5


def _from_julian(j, zone):
    return (dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
            + dt.timedelta(days=j - 2440587.5)).astimezone(zone)


def sun(date=None, lat=LAT, lon=LON, zone=None):
    """(восход, закат) местного времени. None, None — полярный день или полярная ночь."""
    zone = zone or tz()
    date = date or dt.datetime.now(zone).date()
    n = round(_julian(date) - 2451545.0 + 0.0008)
    j_star = n - lon / 360.0
    M = math.radians((357.5291 + 0.98560028 * j_star) % 360)
    C = 1.9148 * math.sin(M) + 0.02 * math.sin(2 * M) + 0.0003 * math.sin(3 * M)
    lam = math.radians((math.degrees(M) + C + 180 + 102.9372) % 360)
    j_transit = 2451545.0 + j_star + 0.0053 * math.sin(M) - 0.0069 * math.sin(2 * lam)
    sin_d = math.sin(lam) * math.sin(math.radians(23.44))
    cos_d = math.cos(math.asin(sin_d))
    cos_w = ((math.sin(math.radians(ZENITH)) - math.sin(math.radians(lat)) * sin_d)
             / (math.cos(math.radians(lat)) * cos_d))
    if abs(cos_w) > 1:
        return None, None
    w = math.degrees(math.acos(cos_w))
    return _from_julian(j_transit - w / 360.0, zone), _from_julian(j_transit + w / 360.0, zone)


def sunset(date=None, **kw):
    return sun(date, **kw)[1]


def sunrise(date=None, **kw):
    return sun(date, **kw)[0]


def daylight(t, date=None, **kw):
    """Светло ли в этот момент (между восходом и закатом)."""
    rise, set_ = sun(date or t.date(), **kw)
    return bool(rise and set_ and rise <= t <= set_)


if __name__ == "__main__":
    d = dt.date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else None
    r, s = sun(d)
    print(f"{(d or dt.datetime.now(tz()).date())}: восход {r:%H:%M}, закат {s:%H:%M} ({TZ}, {LAT}, {LON})")
