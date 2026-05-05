"""Lightweight PII detection based on data values using regex patterns.

Detects common PII in sample data to decide if a field should be masked.
Much lighter than NLP-based solutions (Presidio) for CLI tools.
"""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Regex patterns for common PII
# ---------------------------------------------------------------------------

# China mainland mobile: 11 digits, starts with 1, 2nd digit 3-9
_CHINA_MOBILE = re.compile(r"^1[3-9]\d{9}$")

# International phone: +countrycode + digits, or digits with separators
_INTL_PHONE = re.compile(r"^(?:\+\d{1,4}[-\s]?)?\d{7,15}$")

# Email
_EMAIL = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")

# China ID card: 15 or 18 digits, last can be X
_CHINA_IDCARD = re.compile(r"^\d{15}$|^\d{17}[\dXx]$")

# China bank card: 16-19 digits, Luhn check optional (regex-only here)
_BANK_CARD = re.compile(r"^\d{16,19}$")

# Passport: common formats
_PASSPORT = re.compile(r"^[A-Z]\d{7,9}$|^\d{9}$")

# IPv4
_IPV4 = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$")

# Common Chinese surnames (single character, top ~200)
_CHINESE_SURNAMES_SINGLE = set(
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
    "戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳酆鲍史唐"
    "费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元卜顾孟平黄"
    "和穆萧尹姚邵湛汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞熊纪舒屈项祝董梁"
    "杜阮蓝闵席季麻强贾路娄危江童颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍"
    "万柯卢莫经丁宣贲邓郁单杭洪包诸左石崔吉钮龚荀羊於惠甄麴家封芮羿储"
    "靳汲邴糜松井段富巫乌焦巴弓牧隗山谷车侯宓蓬全郗班仰秋仲伊宫宁仇栾"
    "暴甘钭厉戎祖武符刘景詹束龙叶幸司韶郜黎蓟薄印宿白怀蒲邰从鄂索咸籍"
    "赖卓蔺屠蒙池乔阴鬱胥能苍双闻莘党翟谭贡劳逄姬申扶堵冉宰郦雍却璩桑"
    "桂牛寿通边扈燕冀郏浦尚农温别庄晏柴瞿阎充慕连茹习宦艾鱼容向古易慎"
    "戈廖庾终暨居衡步都耿满弘匡国文寇广禄阙东欧殳沃利蔚越夔隆师巩厍聂"
    "晁勾敖融冷訾辛阚那简饶空曾毋沙乜养鞠须丰巢关蒯相查后荆红游竺权逯"
    "盖益桓公"
)

# Common compound surnames (复姓)
_CHINESE_SURNAMES_COMPOUND = [
    "欧阳", "太史", "端木", "上官", "司马", "东方", "独孤", "南宫", "夏侯", "诸葛",
    "尉迟", "皇甫", "公孙", "慕容", "仲孙", "长孙", "宇文", "司徒", "鲜于", "司空",
    "闾丘", "子车", "亓官", "司寇", "巫马", "颛孙", "壤驷", "公良", "漆雕", "宰父",
    "谷梁", "段干", "百里", "东郭", "南门", "呼延", "归海", "羊舌", "微生", "岳帅",
    "缑亢", "况后", "有琴", "梁丘", "左丘", "东门", "西门", "商牟", "佘佴", "伯赏",
    "万俟", "司马", "上官", "欧阳", "夏侯", "诸葛", "闻人", "东方", "赫连", "皇甫",
    "尉迟", "公羊", "澹台", "公冶", "宗政", "濮阳", "淳于", "单于", "太叔", "申屠",
    "公孙", "仲孙", "轩辕", "令狐", "钟离", "宇文", "长孙", "慕容", "鲜于", "闾丘",
    "司徒", "司空", "亓官", "司寇", "仉督", "子车", "颛孙", "端木", "巫马", "公西",
    "漆雕", "乐正", "壤驷", "公良", "拓跋", "夹谷", "宰父", "谷梁", "段干", "百里",
    "东郭", "南门", "呼延", "归海", "羊舌", "微生", "梁丘", "左丘", "东门", "西门",
    "南宫", "第五",
]

# Build regex for Chinese names:
#   - Single surname + 1-3 char given name  → 2-4 chars total
#   - Compound surname + 1-2 char given name → 3-4 chars total
#   - Allow 5 chars for ethnic minority names (compound + 3 char given name, rare but possible)
_surname_single = "".join(_CHINESE_SURNAMES_SINGLE)
_surname_compound = "|".join(_CHINESE_SURNAMES_COMPOUND)
_CHINESE_NAME = re.compile(
    rf"^(?:[{_surname_single}][\u4e00-\u9fff]{{1,3}}|(?:{_surname_compound})[\u4e00-\u9fff]{{1,2}})$"
)

# Date-like: YYYY-MM-DD or YYYY/MM/DD — used for exclusion only, NOT as PII
_DATE_LIKE = re.compile(
    r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}$|"  # 1990-01-01
    r"^\d{1,2}[-/]\d{1,2}[-/]\d{4}$"   # 01-01-1990
)

# Address detection: contains Chinese admin division keywords
_ADDRESS_KEYWORDS = re.compile(
    r"(?:省|自治区|直辖市|市|地区|自治州|盟|县|自治县|区|市辖区|旗|自治旗|林区|乡|镇|街道|路|街|巷|号|栋|单元|室|层|楼)"
)

# All patterns with labels
_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("china_mobile", _CHINA_MOBILE),
    ("phone", _INTL_PHONE),
    ("email", _EMAIL),
    ("china_idcard", _CHINA_IDCARD),
    ("bank_card", _BANK_CARD),
    ("passport", _PASSPORT),
    ("ipv4", _IPV4),
    ("chinese_name", _CHINESE_NAME),
]


# Column-name based sensitive field detection
# These columns are treated as PII regardless of value patterns
_COL_NAME_SENSITIVE: dict[str, list[str]] = {
    "chinese_name": ["name", "姓名", "联系人", "负责人", "患者姓名"],
    "birth_date": ["dob", "birth", "birthday", "出生日期", "生日", "出生年月", "出生时间"],
    "address": ["address", "addr", "地址", "住址", "居住地", "户籍", "籍贯"],
    "phone": ["phone", "mobile", "tel", "电话", "手机", "手机号", "联系方式", "联系电话"],
    "email": ["email", "mail", "邮箱", "邮件", "电子邮箱", "电邮"],
    "china_idcard": ["idcard", "id_card", "身份证", "身份证号", "证件号", "居民身份证"],
}

# Exclusions for column-name matching (to avoid false positives like username, filename)
_COL_NAME_EXCLUDES: dict[str, list[str]] = {
    "chinese_name": ["username", "groupname", "filename", "classname", "tablename", "columnname", "dbname"],
}


def _detect_pii_by_col_name(col_name: str) -> list[str]:
    """Detect PII type based on column name alone."""
    lower = col_name.lower().replace("_", "")
    results: list[str] = []
    for pii_type, keywords in _COL_NAME_SENSITIVE.items():
        for kw in keywords:
            kw_norm = kw.lower().replace("_", "")
            if lower == kw_norm or lower.endswith(kw_norm) or lower.startswith(kw_norm):
                excludes = _COL_NAME_EXCLUDES.get(pii_type, [])
                if any(ex.lower().replace("_", "") == lower for ex in excludes):
                    continue
                results.append(pii_type)
                break
    return results


def _is_numeric_only(value: str) -> bool:
    """Check if value is just digits (avoid false positive on primary keys)."""
    return value.isdigit()


def _is_likely_pk(value: str, col_name: str) -> bool:
    """Heuristic: small integers or auto-increment patterns are likely PKs, not PII."""
    if value.isdigit():
        num = int(value)
        if num <= 999999:  # small IDs are usually PK
            return True
        # Check column name hints
        pk_hints = ("id", "_id", "no", "code", "seq", "num", "sn", "编号", "序号")
        if any(h in col_name.lower() for h in pk_hints):
            return True
    return False


def detect_pii(value: Any, col_name: str = "") -> list[str]:
    """Detect PII types in a single value.

    Returns a list of matched PII type labels. Empty list means no PII detected.
    """
    if value is None:
        return []

    s = str(value).strip()
    if len(s) < 2:
        return []

    # Skip likely primary keys / identifiers
    if _is_likely_pk(s, col_name):
        return []

    matches: list[str] = []
    for label, pattern in _PATTERNS:
        if pattern.match(s):
            # Extra validation: phone numbers should pass length check
            if label in ("china_mobile", "phone"):
                digits = re.sub(r"\D", "", s)
                if len(digits) < 7 or len(digits) > 15:
                    continue
                # Exclude year-like numbers (e.g. 20240101)
                if digits.startswith("20") and len(digits) == 8:
                    continue
            # Exclude pure numbers that look like codes, not bank cards
            if label == "bank_card" and _is_likely_pk(s, col_name):
                continue
            matches.append(label)

    # Address detection: value contains Chinese admin division keywords
    if _ADDRESS_KEYWORDS.search(s) and len(s) >= 6:
        # Filter out false positives: pure dates or codes
        if not _DATE_LIKE.match(s):
            matches.append("address")

    return matches


def field_has_pii(samples: list[Any], col_name: str = "", threshold: float = 0.3) -> list[str]:
    """Check if a field contains PII based on column name and sample values.

    Priority:
        1. Column-name based detection (dob, name, address, etc.)
        2. Value-pattern based detection (phone numbers, IDs, names in values)

    Args:
        samples: List of sample values from the column
        col_name: Column name for context hints
        threshold: Minimum ratio of PII-matching samples to flag (0-1)

    Returns:
        List of PII type labels found. Empty if below threshold or no PII.
    """
    # First: column-name based detection (e.g. dob, birth_date, name)
    col_based = _detect_pii_by_col_name(col_name)
    if col_based:
        return col_based

    if not samples:
        return []

    # Second: value-pattern based detection
    all_matches: list[str] = []
    for sample in samples:
        matches = detect_pii(sample, col_name)
        all_matches.extend(matches)

    if not all_matches:
        return []

    # If more than threshold ratio of samples match any PII, flag it
    unique_samples = len(set(str(s) for s in samples if s is not None))
    if unique_samples == 0:
        return []

    pii_sample_count = sum(
        1 for s in samples if detect_pii(s, col_name)
    )
    ratio = pii_sample_count / len(samples)

    if ratio >= threshold:
        # Return the most common PII types found
        from collections import Counter
        return [label for label, _ in Counter(all_matches).most_common(3)]

    return []


def _mask_address(value: str) -> str:
    """Mask address: keep province/city/district, mask detailed part."""
    # Match admin division segments (province, city, district, county, etc.)
    admin_pattern = re.compile(
        r"([^省市区县州盟旗\s]+(?:省|自治区|直辖市|市|地区|自治州|盟|县|自治县|区|市辖区|旗|自治旗|林区))"
    )
    matches = list(admin_pattern.finditer(value))

    if not matches:
        # Cannot identify admin divisions; conservative masking
        if len(value) <= 4:
            return value[0] + "*" * (len(value) - 1)
        return value[:2] + "*" * (len(value) - 2)

    last_end = matches[-1].end()
    if last_end >= len(value) - 1:
        # Only admin divisions, no detailed part to mask
        return value

    # Keep admin divisions, mask the rest with fixed-length asterisks
    masked_len = len(value) - last_end
    return value[:last_end] + "*" * max(4, masked_len)


def mask_value(value: Any, pii_types: list[str] | None = None, col_name: str = "") -> Any:
    """Mask a sensitive value with strategy determined by PII type.

    - chinese_name / name fields: keep first char, mask rest
    - address: keep province/city/district, mask detailed part
    - others: default masking (keep first 2 and last 2 for long values)
    """
    if value is None:
        return None
    s = str(value)
    if len(s) <= 1:
        return "*"

    types = pii_types or []
    lower_col = col_name.lower()

    # Name: keep first char, mask rest (Chinese or English names)
    is_name_col = (
        lower_col == "name"
        or lower_col.endswith("_name")
        or lower_col.startswith("name_")
        or "姓名" in lower_col
    )
    if "chinese_name" in types or (is_name_col and "user" not in lower_col and "group" not in lower_col):
        return s[0] + "*" * (len(s) - 1)

    # Address: keep admin divisions, mask detailed part
    if "address" in types or any(k in lower_col for k in ("address", "addr", "地址", "住址")):
        return _mask_address(s)

    # Birth date / date fields: preserve year, mask month/day
    if "birth_date" in types or any(k in lower_col for k in ("dob", "birth", "出生日期", "生日")):
        date_pattern = re.compile(r"^(\d{4})([-/])(\d{1,2})\2(\d{1,2})$")
        m = date_pattern.match(s)
        if m:
            return f"{m.group(1)}{m.group(2)}**{m.group(2)}**"
        # Fallback for non-standard date formats
        if len(s) <= 6:
            return s[0] + "*" * (len(s) - 2) + s[-1]
        return s[:2] + "*" * (len(s) - 4) + s[-2:]

    # Default masking
    if len(s) <= 6:
        return s[0] + "*" * (len(s) - 2) + s[-1]
    return s[:2] + "*" * (len(s) - 4) + s[-2:]
