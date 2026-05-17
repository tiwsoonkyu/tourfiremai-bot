"""Sprint 1 test: country typo normalization."""

import pytest
from v2.lib.country import (
    normalize_country_typo,
    resolve_city_to_country,
    country_id_to_name,
    list_supported_countries,
    COUNTRY_ID_MAP,
)


class TestNormalizeCountryTypo:
    @pytest.mark.parametrize("text,expected_country,expected_id", [
        ("ญี่ปุ่น", "ญี่ปุ่น", 2),
        ("ญีปุ่น", "ญี่ปุ่น", 2),
        ("Japan", "ญี่ปุ่น", 2),
        ("japan", "ญี่ปุ่น", 2),
        ("JP", "ญี่ปุ่น", 2),
        ("เกาหลี", "เกาหลี", 1),
        ("เกาลี", "เกาหลี", 1),
        ("Korea", "เกาหลี", 1),
        ("south korea", "เกาหลี", 1),
        ("ไต้หวัน", "ไต้หวัน", 19),
        ("ใต้หวัน", "ไต้หวัน", 19),
        ("Taiwan", "ไต้หวัน", 19),
        ("เวียดนาม", "เวียดนาม", 7),
        ("vietnam", "เวียดนาม", 7),
        ("ฮ่องกง", "ฮ่องกง", 3),
        ("HK", "ฮ่องกง", 3),
        ("สิงคโปร์", "สิงคโปร์", 4),
        ("singapore", "สิงคโปร์", 4),
        ("จีน", "จีน", 5),
        ("china", "จีน", 5),
        ("มาเลเซีย", "มาเลเซีย", 6),
        ("malaysia", "มาเลเซีย", 6),
    ])
    def test_known_inputs(self, text, expected_country, expected_id):
        country, cid = normalize_country_typo(text)
        assert country == expected_country
        assert cid == expected_id

    def test_unknown_returns_none(self):
        assert normalize_country_typo("ดวงจันทร์") == (None, None)
        assert normalize_country_typo("Mars") == (None, None)

    def test_empty_returns_none(self):
        assert normalize_country_typo("") == (None, None)
        assert normalize_country_typo(None) == (None, None)

    def test_embedded_in_sentence(self):
        country, cid = normalize_country_typo("อยากไปญี่ปุ่นเดือนหน้า")
        assert country == "ญี่ปุ่น"
        assert cid == 2

    def test_city_implies_country(self):
        country, cid = normalize_country_typo("อยากไปโตเกียว")
        assert country == "ญี่ปุ่น"
        assert cid == 2

    def test_longest_match_wins(self):
        # "เกาหลีใต้" should resolve to เกาหลี (not partial match like เกาห์ลี)
        country, cid = normalize_country_typo("เกาหลีใต้")
        assert country == "เกาหลี"


class TestResolveCityToCountry:
    @pytest.mark.parametrize("city,country,cid", [
        ("โตเกียว", "ญี่ปุ่น", 2),
        ("ฮอกไกโด", "ญี่ปุ่น", 2),
        ("คิวชู", "ญี่ปุ่น", 2),
        ("โซล", "เกาหลี", 1),
        ("ปูซาน", "เกาหลี", 1),
        ("เซี่ยงไฮ้", "จีน", 5),
        ("เฉิงตู", "จีน", 5),
        ("Tokyo", "ญี่ปุ่น", 2),
        ("seoul", "เกาหลี", 1),
    ])
    def test_known_cities(self, city, country, cid):
        result_country, result_cid = resolve_city_to_country(city)
        assert result_country == country
        assert result_cid == cid

    def test_unknown_city(self):
        assert resolve_city_to_country("Paris") == (None, None)


class TestSupportingHelpers:
    def test_country_id_to_name_known(self):
        assert country_id_to_name(2) == "ญี่ปุ่น"
        assert country_id_to_name(19) == "ไต้หวัน"

    def test_country_id_to_name_unknown(self):
        assert country_id_to_name(999) is None

    def test_list_supported_countries(self):
        result = list_supported_countries()
        assert len(result) == 8
        assert all("country_id" in r and "canonical_name" in r and "alias_count" in r for r in result)

    def test_no_country_id_collisions(self):
        ids = list(COUNTRY_ID_MAP.values())
        assert len(ids) == len(set(ids)), "country_id values must be unique"
