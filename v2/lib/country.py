"""
v2.lib.country — Country normalization for TourFireMai V2

Ports V1's normalize_country_typo() and extends with:
- Strict canonical name + country_id mapping
- City → country resolution (Tokyo → Japan)
- Fuzzy alias matching (Thai typos, English aliases)

Public API:
    normalize_country_typo(text: str) -> (canonical_name, country_id) | (None, None)
    resolve_city_to_country(city: str) -> (canonical_name, country_id) | (None, None)
    list_supported_countries() -> list[dict]
"""

from __future__ import annotations

import unicodedata
from typing import Optional, Tuple

# Canonical mapping from tourfiremai.com URL pattern:
# /intertour/{country_id}/{country_thai_name}
COUNTRY_ID_MAP: dict[str, int] = {
    "เกาหลี": 1,
    "ญี่ปุ่น": 2,
    "ฮ่องกง": 3,
    "สิงคโปร์": 4,
    "จีน": 5,
    "มาเลเซีย": 6,
    "เวียดนาม": 7,
    "ไต้หวัน": 19,
}

ID_TO_COUNTRY: dict[int, str] = {v: k for k, v in COUNTRY_ID_MAP.items()}

COUNTRY_ALIASES: dict[str, list[str]] = {
    "เกาหลี": [
        "เกาหลี", "เกาลี", "เกาห์ลี", "เกาหรี",
        "korea", "south korea", "kor", "rok", "เกาหลีใต้",
    ],
    "ญี่ปุ่น": [
        "ญี่ปุ่น", "ญีปุ่น", "ญี่ปุน", "ญี่ปุ่ณ", "ญี่ป่น", "ยี่ปุ่น",
        "japan", "jp", "jpn", "nippon", "nihon",
    ],
    "ฮ่องกง": [
        "ฮ่องกง", "ฮองกง", "ฮ่อง", "hongkong", "hong kong", "hk", "h.k.",
    ],
    "สิงคโปร์": [
        "สิงคโปร์", "สิงคโปร", "สิงคโป", "singapore", "sg", "spore",
    ],
    "จีน": [
        "จีน", "ประเทศจีน",
        "china", "prc", "cn",
    ],
    "มาเลเซีย": [
        "มาเลเซีย", "มาเลย์", "มาเล", "malaysia", "my", "mys",
    ],
    "เวียดนาม": [
        "เวียดนาม", "เวียดนาน", "เวียดนาด", "เหวียดนาม",
        "vietnam", "viet", "vn", "vnm",
    ],
    "ไต้หวัน": [
        "ไต้หวัน", "ใต้หวัน", "ไต้ห์วัน", "ไตหวัน",
        "taiwan", "tw", "twn", "roc",
    ],
}

CITY_TO_COUNTRY: dict[str, str] = {
    "โตเกียว": "ญี่ปุ่น", "tokyo": "ญี่ปุ่น",
    "โอซาก้า": "ญี่ปุ่น", "osaka": "ญี่ปุ่น",
    "เกียวโต": "ญี่ปุ่น", "kyoto": "ญี่ปุ่น",
    "ฮอกไกโด": "ญี่ปุ่น", "hokkaido": "ญี่ปุ่น", "ซัปโปโร": "ญี่ปุ่น", "sapporo": "ญี่ปุ่น",
    "นาโกย่า": "ญี่ปุ่น", "nagoya": "ญี่ปุ่น",
    "ฟุกุโอกะ": "ญี่ปุ่น", "fukuoka": "ญี่ปุ่น",
    "คิวชู": "ญี่ปุ่น", "kyushu": "ญี่ปุ่น",
    "โซล": "เกาหลี", "seoul": "เกาหลี",
    "ปูซาน": "เกาหลี", "busan": "เกาหลี",
    "เชจู": "เกาหลี", "jeju": "เกาหลี",
    "เซี่ยงไฮ้": "จีน", "shanghai": "จีน",
    "ปักกิ่ง": "จีน", "beijing": "จีน",
    "เฉิงตู": "จีน", "chengdu": "จีน",
    "คุนหมิง": "จีน", "kunming": "จีน",
    "จางเจียเจี้ย": "จีน",
    "กุ้ยหลิน": "จีน", "guilin": "จีน",
    "โฮจิมินห์": "เวียดนาม", "ฮานอย": "เวียดนาม", "ดานัง": "เวียดนาม",
    "hcmc": "เวียดนาม", "saigon": "เวียดนาม", "hanoi": "เวียดนาม", "danang": "เวียดนาม",
    "ไทเป": "ไต้หวัน", "เกาสง": "ไต้หวัน", "เถาหยวน": "ไต้หวัน",
    "taipei": "ไต้หวัน", "kaohsiung": "ไต้หวัน",
}


def _normalize(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFC", s)
    s = s.strip()
    return "".join(c.lower() if c.isascii() else c for c in s)


def normalize_country_typo(text: str) -> Tuple[Optional[str], Optional[int]]:
    if not text:
        return None, None

    normalized = _normalize(text)

    matches: list[tuple[str, str, int]] = []
    for canonical, aliases in COUNTRY_ALIASES.items():
        for alias in aliases:
            alias_norm = _normalize(alias)
            if alias_norm and alias_norm in normalized:
                matches.append((alias_norm, canonical, COUNTRY_ID_MAP[canonical]))

    if matches:
        matches.sort(key=lambda m: len(m[0]), reverse=True)
        _, canonical, country_id = matches[0]
        return canonical, country_id

    return resolve_city_to_country(text)


def resolve_city_to_country(city_text: str) -> Tuple[Optional[str], Optional[int]]:
    if not city_text:
        return None, None

    normalized = _normalize(city_text)
    matches: list[tuple[str, str]] = []
    for city, country in CITY_TO_COUNTRY.items():
        city_norm = _normalize(city)
        if city_norm and city_norm in normalized:
            matches.append((city_norm, country))
    if matches:
        matches.sort(key=lambda m: len(m[0]), reverse=True)
        _, country = matches[0]
        return country, COUNTRY_ID_MAP[country]
    return None, None


def country_id_to_name(country_id: int) -> Optional[str]:
    return ID_TO_COUNTRY.get(country_id)


def list_supported_countries() -> list[dict]:
    return [
        {"country_id": cid, "canonical_name": name, "alias_count": len(COUNTRY_ALIASES[name])}
        for name, cid in COUNTRY_ID_MAP.items()
    ]
