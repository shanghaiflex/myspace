#!/usr/bin/env python3
"""Проверка палитры графиков (--c1…--c6 и нейтраль --c0 в theme.css).

CLAUDE.md требует прогонять валидатор при каждой правке цветов данных, но самого валидатора в репозитории
не было — он жил разовым скриптом. Теперь он здесь, и цифры из его отчёта и есть то, что записано в
комментарии к палитре.

Шесть проверок, все на одном наборе:
  1. светлота в полосе          — ни один цвет не темнее и не светлее остальных настолько, чтобы на
                                  графике читаться «другим типом», а не другим цветом;
  2. цветность выше пола        — серых среди данных нет, иначе они сливаются с --c0 («прочее»);
  3. соседние пары, полное зрение — слоты назначаются подряд, поэтому критична именно соседняя пара;
  4. соседние пары, дальтонизм  — то же при протанопии и дейтеранопии (Machado 2009, severity 1.0).
                                  Тританопия считается и печатается справкой, но порогом не является:
                                  она редка (порядка 1 на 10 000 против 1 на 12 мужчин), и ни один
                                  разумный набор из шести цветов её не проходит — палитра сайта не
                                  проходила её и до журнальной перекраски (8,5);
  5. контраст к светлой бумаге  — ≥ 3:1 (WCAG для нетекстовой графики);
  6. контраст к тёмной бумаге   — тот же набор служит и в тёмной теме.

Расстояние — ΔE в OKLab, умноженное на 100 (так 0,124 из расчёта читается как 12,4).

    python3 scripts/palette_check.py              # палитра из theme.css
    python3 scripts/palette_check.py '#c0362a' '#a94f8f' …   # проверить свой набор
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
THEME = ROOT / "theme.css"

LIGHT_BG = "#ffffff"   # --surface светлой темы: карточка, на которой стоит график
DARK_BG = "#1b1916"    # --surface тёмной темы
L_BAND = (0.42, 0.72)  # светлота OKLab
C_FLOOR = 0.06         # цветность OKLab
DE_NORMAL = 15.0       # худшая соседняя пара при полном зрении
DE_CVD = 10.0          # …и при дальтонизме
CONTRAST = 3.0

# Machado, Oliveira, Fernandes (2009), severity 1.0, в линейном RGB.
CVD = {
    "протанопия": ((0.152286, 1.052583, -0.204868),
                   (0.114503, 0.786281, 0.099216),
                   (-0.003882, -0.048116, 1.051998)),
    "дейтеранопия": ((0.367322, 0.860646, -0.227968),
                     (0.280085, 0.672501, 0.047413),
                     (-0.011820, 0.042940, 0.968881)),
    "тританопия": ((1.255528, -0.076749, -0.178779),
                   (-0.078411, 0.930809, 0.147602),
                   (0.004733, 0.691367, 0.303900)),
}


def srgb(hex_color):
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def to_linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def linear(rgb):
    return tuple(to_linear(c) for c in rgb)


def oklab(rgb):
    r, g, b = linear(rgb)
    l = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
            1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
            0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s)


def delta_e(a, b):
    la, aa, ba = oklab(a)
    lb, ab, bb = oklab(b)
    return 100 * ((la - lb) ** 2 + (aa - ab) ** 2 + (ba - bb) ** 2) ** 0.5


def chroma(rgb):
    _, a, b = oklab(rgb)
    return (a * a + b * b) ** 0.5


def luminance(rgb):
    r, g, b = linear(rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def simulate(rgb, kind):
    m = CVD[kind]
    r, g, b = linear(rgb)
    out = []
    for row in m:
        v = row[0] * r + row[1] * g + row[2] * b
        v = min(1.0, max(0.0, v))
        out.append(1.055 * v ** (1 / 2.4) - 0.055 if v > 0.0031308 else 12.92 * v)
    return tuple(out)


def read_palette():
    css = THEME.read_text(encoding="utf-8")
    colors = []
    for i in range(1, 7):
        m = re.search(rf"--c{i}:\s*(#[0-9a-fA-F]{{6}})", css)
        if not m:
            sys.exit(f"в theme.css нет --c{i}")
        colors.append(m.group(1))
    neutral = re.search(r"--c0:\s*(#[0-9a-fA-F]{6})", css).group(1)
    return colors, neutral


def main():
    args = [a for a in sys.argv[1:] if a.startswith("#")]
    if args:
        colors, neutral = args[:6], (args[6] if len(args) > 6 else read_palette()[1])
    else:
        colors, neutral = read_palette()
    rgbs = [srgb(c) for c in colors]
    ok = True

    print("палитра:", " ".join(colors), "· нейтраль", neutral)

    ls = [oklab(c)[0] for c in rgbs]
    print(f"1. светлота {min(ls):.3f}…{max(ls):.3f} (полоса {L_BAND[0]}…{L_BAND[1]})", end=" ")
    good = all(L_BAND[0] <= l <= L_BAND[1] for l in ls)
    ok &= good
    print("✓" if good else "✗")

    cs = [chroma(c) for c in rgbs]
    print(f"2. цветность минимум {min(cs):.3f} (пол {C_FLOOR})", end=" ")
    good = min(cs) >= C_FLOOR
    ok &= good
    print("✓" if good else "✗")

    pairs = [(i, i + 1) for i in range(len(rgbs) - 1)]
    worst = min(((delta_e(rgbs[i], rgbs[j]), i, j) for i, j in pairs), key=lambda t: t[0])
    print(f"3. соседние пары, полное зрение: худшая ΔE {worst[0]:.1f} "
          f"(c{worst[1] + 1}–c{worst[2] + 1}, порог {DE_NORMAL})", end=" ")
    good = worst[0] >= DE_NORMAL
    ok &= good
    print("✓" if good else "✗")

    worst_cvd = None
    for kind in ("протанопия", "дейтеранопия"):
        sims = [simulate(c, kind) for c in rgbs]
        for i, j in pairs:
            d = delta_e(sims[i], sims[j])
            if worst_cvd is None or d < worst_cvd[0]:
                worst_cvd = (d, kind, i, j)
    print(f"4. соседние пары, дальтонизм: худшая ΔE {worst_cvd[0]:.1f} "
          f"({worst_cvd[1]}, c{worst_cvd[2] + 1}–c{worst_cvd[3] + 1}, порог {DE_CVD})", end=" ")
    good = worst_cvd[0] >= DE_CVD
    ok &= good
    print("✓" if good else "✗")

    sims = [simulate(c, "тританопия") for c in rgbs]
    tri = min((delta_e(sims[i], sims[j]), i, j) for i, j in pairs)
    print(f"   справка: тританопия ΔE {tri[0]:.1f} (c{tri[1] + 1}–c{tri[2] + 1}), порогом не считается")

    for n, bg in ((5, LIGHT_BG), (6, DARK_BG)):
        worst_bg = min((contrast(c, srgb(bg)), i) for i, c in enumerate(rgbs + [srgb(neutral)]))
        name = f"c{worst_bg[1] + 1}" if worst_bg[1] < len(rgbs) else "c0"
        print(f"{n}. контраст к {bg}: минимум {worst_bg[0]:.2f}:1 ({name}, порог {CONTRAST}:1)", end=" ")
        good = worst_bg[0] >= CONTRAST
        ok &= good
        print("✓" if good else "✗")

    print("итог:", "палитра проходит все шесть проверок" if ok else "ПАЛИТРА НЕ ПРОХОДИТ")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
