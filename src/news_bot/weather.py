"""Ban tin thoi tiet - Open-Meteo.

Vi sao tach hoan toan khoi LangGraph: khong co bai de gom trung, khong co gi de
xep hang, va khong goi LLM. Nhet vao graph chi lam graph phuc tap ma khong duoc
gi. Airflow chay no nhu mot task rieng, nen graph tin tuc hong thi van co du bao.

Open-Meteo mien phi, khong can API key, khong gioi han cho muc dung nay.
"""
from __future__ import annotations

from datetime import date

import httpx

from .config import get_settings
from .logging_setup import get_logger
from .models import Digest
from .utils import today_in

log = get_logger(__name__)

API_URL = "https://api.open-meteo.com/v1/forecast"

# WMO weather code -> (mo ta, icon). Nguon: bang ma WW cua Open-Meteo.
WMO: dict[int, tuple[str, str]] = {
    0: ("Trời quang", "☀️"),
    1: ("Nắng nhẹ", "🌤️"), 2: ("Có mây", "⛅"), 3: ("Nhiều mây", "☁️"),
    45: ("Sương mù", "🌫️"), 48: ("Sương mù đóng băng", "🌫️"),
    51: ("Mưa phùn nhẹ", "🌦️"), 53: ("Mưa phùn", "🌦️"), 55: ("Mưa phùn dày", "🌦️"),
    61: ("Mưa nhẹ", "🌧️"), 63: ("Mưa", "🌧️"), 65: ("Mưa to", "🌧️"),
    66: ("Mưa lạnh", "🌧️"), 67: ("Mưa lạnh nặng hạt", "🌧️"),
    71: ("Tuyết nhẹ", "🌨️"), 73: ("Tuyết", "🌨️"), 75: ("Tuyết dày", "🌨️"),
    80: ("Mưa rào nhẹ", "🌦️"), 81: ("Mưa rào", "🌧️"), 82: ("Mưa rào rất to", "⛈️"),
    95: ("Dông", "⛈️"), 96: ("Dông kèm mưa đá", "⛈️"), 99: ("Dông mưa đá lớn", "⛈️"),
}

DAILY_FIELDS = (
    "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,"
    "precipitation_probability_max,wind_speed_10m_max,uv_index_max,"
    "sunrise,sunset"
)
CURRENT_FIELDS = "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code"


def describe(code: int | None) -> tuple[str, str]:
    return WMO.get(int(code or 0), ("Không rõ", "🌡️"))


def fetch_forecast(latitude: float, longitude: float, timezone: str) -> dict:
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "daily": DAILY_FIELDS,
        "current": CURRENT_FIELDS,
        "timezone": timezone,
        "forecast_days": 2,
    }
    with httpx.Client(timeout=20.0) as client:
        resp = client.get(API_URL, params=params)
        resp.raise_for_status()
        return resp.json()


def _advice(code: int, rain_mm: float, rain_prob: int, uv: float,
            t_max: float) -> list[str]:
    """Vai dong khuyen nghi. Thuan quy tac - khong can LLM cho viec nay."""
    tips: list[str] = []
    if code >= 95:
        tips.append("Có dông: tránh ra đường lúc mưa to, cất xe nơi an toàn.")
    if rain_prob >= 60 or rain_mm >= 5:
        tips.append("Khả năng mưa cao — mang theo áo mưa.")
    elif rain_prob >= 30:
        tips.append("Có thể mưa rải rác, nên thủ sẵn áo mưa.")
    if uv >= 8:
        tips.append(f"Chỉ số UV {uv:.0f} (rất cao) — che chắn khi ra ngoài buổi trưa.")
    if t_max >= 35:
        tips.append("Nắng nóng gay gắt, uống đủ nước.")
    if not tips:
        tips.append("Thời tiết thuận lợi, không có gì đáng ngại.")
    return tips


def build_weather_digest(digest_date: date | None = None) -> Digest | None:
    """Tra ve Digest nhom `weather`, hoac None neu tat hoac goi API that bai."""
    s = get_settings()
    if not s.weather_enabled:
        return None

    day = digest_date or today_in(s.news_timezone)
    try:
        data = fetch_forecast(s.weather_latitude, s.weather_longitude, s.news_timezone)
    except Exception as exc:  # thoi tiet hong khong duoc lam hong ca run
        log.warning("weather.failed", error=repr(exc))
        return None

    daily, current = data["daily"], data.get("current", {})
    idx = 0
    if day.isoformat() in daily["time"]:
        idx = daily["time"].index(day.isoformat())

    code = int(daily["weather_code"][idx] or 0)
    label, icon = describe(code)
    t_min = daily["temperature_2m_min"][idx]
    t_max = daily["temperature_2m_max"][idx]
    rain_mm = daily["precipitation_sum"][idx] or 0.0
    rain_prob = int(daily["precipitation_probability_max"][idx] or 0)
    wind = daily["wind_speed_10m_max"][idx]
    uv = daily["uv_index_max"][idx] or 0.0
    sunrise = (daily["sunrise"][idx] or "")[-5:]
    sunset = (daily["sunset"][idx] or "")[-5:]

    now_t = current.get("temperature_2m")
    feels = current.get("apparent_temperature")
    humidity = current.get("relative_humidity_2m")

    overview = f"{icon} {label} · {t_min:.0f}–{t_max:.0f}°C"
    if now_t is not None:
        overview += f" · hiện {now_t:.0f}°C"
        if feels is not None:
            overview += f" (cảm giác {feels:.0f}°C)"

    stats = {
        "place": s.weather_place,
        "code": code,
        "label": label,
        "icon": icon,
        "t_min": t_min,
        "t_max": t_max,
        "now": now_t,
        "feels_like": feels,
        "humidity": humidity,
        "rain_mm": rain_mm,
        "rain_prob": rain_prob,
        "wind_kmh": wind,
        "uv": uv,
        "sunrise": sunrise,
        "sunset": sunset,
        "advice": _advice(code, rain_mm, rain_prob, uv, t_max),
    }

    log.info("weather.built", place=s.weather_place, code=code,
             t_min=t_min, t_max=t_max, rain_prob=rain_prob)
    return Digest(
        group="weather",
        digest_date=day.isoformat(),
        headline=f"{s.weather_place}: {label}, {t_min:.0f}–{t_max:.0f}°C",
        overview=overview,
        items=[],
        stats=stats,
    )
