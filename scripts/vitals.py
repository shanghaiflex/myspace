#!/usr/bin/env python3
"""Ночные показатели, форма и вес — то, что HealthKit знал, а заметка нет (29.09.2026).

До этого дня приложение слало только сон, пульс покоя, HRV, шаги, калории и сам факт тренировки. Цель
«половинка триатлона» мерилась тем, был ли заплыв, а начинающаяся простуда была видна только задним числом.
Теперь BoW шлёт ещё пять видов (`MetricKind` в приложении), и здесь они превращаются в три блока:

  Ночь против нормы. Температура запястья во сне (одно число на ночь, абсолютное), частота дыхания и SpO₂
  за ночь — против моей медианы за BASE_NIGHTS предыдущих ночей, плюс пульс покоя и HRV против их 28 дней.
  Температура +0,5° или дыхание выше нормы вместе с пульсом/HRV — это SICK: тело с чем-то борется, чаще
  всего за день-два до того, как простуду видно. Это не диагноз: то же даёт алкоголь или жаркая спальня,
  поэтому промпт велит говорить «лёгкий день», а не «ты заболел». Одна нагрузка (пульс↑ HRV↓ после
  забега) под этот признак не попадает: нужен хотя бы один из двух признаков, которых тренировка не даёт.

  Форма по дисциплинам. Темп бега, плавания и скорость велосипеда за FORM_DAYS: медиана первой половины
  против второй и последняя тренировка. «Вернуть плавание» — это не только «был заплыв», но и 2:30/100 м
  против 2:50.

  VO₂max и вес — последнее значение против того, что было VO2_BACK / WEIGHT_BACK дней назад.

  vitals.py digest      блок для заметки (его подставляет health.digest)
  vitals.py json        то же данными
"""
import datetime as dt
import json
import os
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import health as H  # noqa: E402

BASE_NIGHTS = 28
MIN_BASE = 7            # ночей, без которых нормы ещё нет и сравнивать не с чем
NIGHT_FROM, NIGHT_TO = 21, 10   # дыхание и SpO₂ считаются ночными в эти часы
TEMP_SICK = 0.5         # °C выше нормы — сильный признак сам по себе
TEMP_SOFT = 0.3         # °C — признак только вместе с другим
RESP_UP = 1.0           # вдохов/мин выше нормы
RHR_UP = 5              # уд/мин выше нормы
HRV_DOWN = 0.8          # доля нормы
FORM_DAYS = 90
FORM_KINDS = {"running": "бег", "swimming": "плавание", "cycling": "велосипед"}
# Короче этого — не тренировка на форму, а дорога: городской велосипед до метро тянул медиану к 12 км/ч.
FORM_MIN_M = {"running": 3000, "swimming": 400, "cycling": 15000}
VO2_BACK = 90
WEIGHT_BACK = 30


def median(vals):
    vals = [v for v in vals if v is not None]
    return statistics.median(vals) if vals else None


def night_values(db, days, today):
    """{утро: {"temp", "resp", "spo2", "spo2min"}} за последние `days` ночей."""
    since = dt.datetime.combine(today - dt.timedelta(days=days), dt.time(0), H.tz())
    out = {}
    for kind in ("wrist_temperature", "respiratory_rate", "oxygen_saturation"):
        for r in H.rows(db, since, "metric", kind):
            v = r["value"]
            if v is None:
                continue
            if kind == "wrist_temperature":
                key = H.sleep_key(r["end"])
            else:
                h = H.local(r["start"]).hour
                if NIGHT_TO <= h < NIGHT_FROM:
                    continue
                key = H.sleep_key(r["start"])
                if kind == "oxygen_saturation" and v <= 1.0:
                    v *= 100
            out.setdefault(key, {}).setdefault(kind, []).append(v)
    res = {}
    for key, d in out.items():
        spo2 = d.get("oxygen_saturation") or []
        res[key] = {"temp": median(d.get("wrist_temperature") or []),
                    "resp": median(d.get("respiratory_rate") or []),
                    "spo2": median(spo2), "spo2min": min(spo2) if spo2 else None}
    return res


def night_status(db, today=None):
    """Последняя ночь против нормы и признаки того, что тело с чем-то борется."""
    today = today or dt.datetime.now(H.tz()).date()
    nv = night_values(db, BASE_NIGHTS + 3, today)
    if not nv:
        return None
    last_key = max(nv)
    if (today - dt.date.fromisoformat(last_key)).days > 1:
        return None                      # последней ночи нет — нечего сравнивать сегодня
    last = nv[last_key]
    prior = [v for k, v in nv.items() if k < last_key]
    base = {k: median([p[k] for p in prior]) if len([p for p in prior if p[k] is not None]) >= MIN_BASE else None
            for k in ("temp", "resp", "spo2")}
    days = H.daily(db, BASE_NIGHTS + 1, dt.date.fromisoformat(last_key))
    day, rest = days[0], [d for d in days[1:] if not d["noWatch"]]
    rhr_base, hrv_base = median([d["rhr"] for d in rest]), median([d["hrv"] for d in rest])

    dev = {"temp": round(last["temp"] - base["temp"], 2) if last["temp"] is not None and base["temp"] is not None else None,
           "resp": round(last["resp"] - base["resp"], 1) if last["resp"] is not None and base["resp"] is not None else None,
           "rhr": day["rhr"] - rhr_base if day["rhr"] and rhr_base else None,
           "hrv": round(day["hrv"] / hrv_base, 2) if day["hrv"] and hrv_base else None}
    signs = []
    if dev["temp"] is not None and dev["temp"] >= TEMP_SOFT:
        signs.append(f"температура запястья {dev['temp']:+.1f}° к норме".replace(".", ","))
    if dev["resp"] is not None and dev["resp"] >= RESP_UP:
        signs.append(f"дыхание во сне {last['resp']:.1f} против обычных {base['resp']:.1f}".replace(".", ","))
    if dev["rhr"] is not None and dev["rhr"] >= RHR_UP:
        signs.append(f"пульс покоя {day['rhr']} против {round(rhr_base)}")
    if dev["hrv"] is not None and dev["hrv"] <= HRV_DOWN:
        signs.append(f"HRV {day['hrv']} против {round(hrv_base)}")
    # Тренировка сама поднимает пульс и роняет HRV; температуру и дыхание — нет. Без них это нагрузка, а не простуда.
    body = (dev["temp"] or 0) >= TEMP_SOFT or (dev["resp"] or 0) >= RESP_UP
    sick = (dev["temp"] or 0) >= TEMP_SICK or (body and len(signs) >= 2)
    return {"night": last_key, "last": last, "base": base, "dev": dev, "signs": signs, "sick": sick,
            "rhrBase": rhr_base, "hrvBase": hrv_base}


def said_today(db, today):
    """Время первой сегодняшней заметки, написанной при ночных признаках, — или None."""
    first = None
    for r in db.execute("SELECT ts, snapshot FROM reviews WHERE kind='note' AND snapshot IS NOT NULL ORDER BY id DESC LIMIT 30"):
        try:
            snap = json.loads(r["snapshot"])
        except (TypeError, ValueError):
            continue
        if snap.get("date") == today.isoformat() and snap.get("sick"):
            first = r["ts"]
    return H.local(first).strftime("%H:%M") if first else None


def form(db, today=None):
    """Темп по дисциплинам триатлона за FORM_DAYS: первая половина против второй, последняя тренировка."""
    today = today or dt.datetime.now(H.tz()).date()
    since = dt.datetime.combine(today - dt.timedelta(days=FORM_DAYS), dt.time(0), H.tz())
    half = today - dt.timedelta(days=FORM_DAYS // 2)
    by = {}
    for r in H.rows(db, since, "workout"):
        k = H.workout_kind(r["kind"])
        if k not in FORM_KINDS:
            continue
        raw = json.loads(r["data"])
        m, dist = r["value"] or 0, raw.get("distanceMeters") or 0
        if not H.pace(k, m, dist) or dist < FORM_MIN_M[k] or H.implausible(k, m, dist, raw.get("averageHeartRate")):
            continue
        # Для сравнения — минуты на единицу дистанции (для велосипеда наоборот, скорость), чтобы медиана была числом.
        per = m / (dist / (100 if k == "swimming" else 1000)) if k != "cycling" else dist / 1000 / (m / 60)
        by.setdefault(k, []).append({"date": H.local(r["start"]).date(), "per": per, "text": H.pace(k, m, dist),
                                     "km": round(dist / 1000, 2), "where": raw.get("swimLocation")})
    out = {}
    for k, ws in by.items():
        ws.sort(key=lambda w: w["date"])
        early = median([w["per"] for w in ws if w["date"] < half])
        late = median([w["per"] for w in ws if w["date"] >= half])
        out[k] = {"n": len(ws), "km": round(sum(w["km"] for w in ws), 1), "last": ws[-1], "early": early, "late": late}
    return out


def fmt_per(kind, v):
    if v is None:
        return "—"
    if kind == "cycling":
        return f"{v:.1f} км/ч".replace(".", ",")
    return f"{int(v)}:{round(v % 1 * 60):02d}" + ("/100м" if kind == "swimming" else "/км")


def trend(db, kind, back, today=None):
    """Последнее значение и то, что было `back` дней назад (ближайшее не позже той даты)."""
    today = today or dt.datetime.now(H.tz()).date()
    since = dt.datetime.combine(today - dt.timedelta(days=365), dt.time(0), H.tz())
    rs = [(H.local(r["start"]).date(), r["value"]) for r in H.rows(db, since, "metric", kind) if r["value"]]
    if not rs:
        return None
    last_d = rs[-1][0]
    # Вес меряется иногда по нескольку раз в день — берётся медиана последней недели, а не случайное взвешивание.
    now = median([v for d, v in rs if d > last_d - dt.timedelta(days=7)])
    then_d = last_d - dt.timedelta(days=back)
    old = [(d, v) for d, v in rs if d <= then_d]
    then = median([v for d, v in old if d > old[-1][0] - dt.timedelta(days=7)]) if old else None
    return {"date": last_d, "now": now, "then": then, "thenDate": old[-1][0] if old else None}


def digest(db=None, today=None):
    db = db or H.connect()
    today = today or dt.datetime.now(H.tz()).date()
    lines = []
    ns = night_status(db, today)
    if ns:
        b, l = ns["base"], ns["last"]
        bits = []
        if l["temp"] is not None and ns["dev"]["temp"] is not None:
            bits.append(f"температура запястья {ns['dev']['temp']:+.2f}° к норме".replace(".", ","))
        if l["resp"] is not None:
            bits.append(f"дыхание {l['resp']:.1f}/мин".replace(".", ",") + (f" (норма {b['resp']:.1f})".replace(".", ",") if b["resp"] else ""))
        if l["spo2"] is not None:
            bits.append(f"SpO₂ {l['spo2']:.0f}%, минимум {l['spo2min']:.0f}%" + (f" (норма {b['spo2']:.0f}%)" if b["spo2"] else ""))
        if bits:
            lines.append(f"Ночь на {ns['night'][8:]}.{ns['night'][5:7]} против моей нормы за {BASE_NIGHTS} ночей: " + "; ".join(bits) + ".")
        if ns["sick"]:
            # Температура и дыхание меряются только во сне, одно число на ночь: днём они не обновляются, и «признаки
            # всё ещё держатся» сказать нечем. 06.10.2026 заметка повторяла ночную строку каждый час как текущее
            # состояние, хотя дневные HRV и пульс покоя к обеду вернулись к норме.
            lines.append("ПРИЗНАКИ прошлой ночи: тело с чем-то борется — " + ", ".join(ns["signs"])
                         + ". Так обычно выглядит начало простуды (или алкоголь, или жаркая ночь) — это не диагноз."
                         + " Это одно измерение за ночь: днём оно не обновляется, следующее будет только после сна.")
            said = said_today(db, today)
            if said:
                day = H.daily(db, 1, today)[0]
                now = [f"HRV {day['hrv']} (норма {round(ns['hrvBase'])})" if day["hrv"] and ns["hrvBase"] else None,
                       f"пульс покоя {day['rhr']} (норма {round(ns['rhrBase'])})" if day["rhr"] and ns["rhrBase"] else None]
                lines.append(f"Про ночные признаки уже сказано в заметке в {said}. Не повторяй их и не пиши «всё ещё» —"
                             " судить о дне можно только по дневным цифрам"
                             + (": " + ", ".join(x for x in now if x) if any(now) else "") + ".")
        elif ns["signs"]:
            lines.append("Отклонения без общей картины: " + ", ".join(ns["signs"]) + ".")
    fm = form(db, today)
    if fm:
        parts = []
        for k in ("swimming", "cycling", "running"):
            f = fm.get(k)
            if not f:
                continue
            s = f"{FORM_KINDS[k]}: {f['n']} трен., " + f"{f['km']} км".replace(".", ",") + f", последняя {f['last']['date']:%d.%m} {f['last']['text']}"
            if f["early"] and f["late"]:
                s += f"; медиана темпа {fmt_per(k, f['early'])} → {fmt_per(k, f['late'])}"
            parts.append(s)
        if parts:
            lines.append(f"Форма за {FORM_DAYS} дней (первая половина → вторая): " + "; ".join(parts) + ".")
    vo = trend(db, "vo2_max", VO2_BACK, today)
    if vo:
        lines.append(f"VO₂max {vo['now']:.1f} ({vo['date']:%d.%m})".replace(".", ",", 1)
                     + (f", {VO2_BACK} дней назад {vo['then']:.1f}".replace(".", ",") if vo["then"] else ""))
    wt = trend(db, "body_mass", WEIGHT_BACK, today)
    if wt and (today - wt["date"]).days <= 30:
        lines.append(f"Вес {wt['now']:.1f} кг".replace(".", ",") + (f", месяц назад {wt['then']:.1f}".replace(".", ",") if wt["then"] else ""))
    return "\n".join(lines)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "digest"
    db = H.connect()
    if cmd == "json":
        print(json.dumps({"night": night_status(db), "form": form(db),
                          "vo2": trend(db, "vo2_max", VO2_BACK), "weight": trend(db, "body_mass", WEIGHT_BACK)},
                         ensure_ascii=False, indent=1, default=str))
    else:
        print(digest(db) or "(данных пока нет)")


if __name__ == "__main__":
    main()
