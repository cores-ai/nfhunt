import re
import json
import html
import unicodedata
def _decode_unicode_escape(match):
    try:
        return chr(int(match.group(1), 16))
    except Exception:
        return match.group(0)


def _decode_hex_escape(match):
    try:
        return chr(int(match.group(1), 16))
    except Exception:
        return match.group(0)


def decode_netflix_value(value):
    if value is None:
        return None
    cleaned = html.unescape(str(value))
    replacements = {
        "\\x20": " ",
        "\\u00A0": " ",
        "\\u00a0": " ",
        "&nbsp;": " ",
        "u00A0": " ",
    }
    for source, target in replacements.items():
        cleaned = cleaned.replace(source, target)
    cleaned = cleaned.replace("\\/", "/").replace('\\"', '"').replace("\\n", " ").replace("\\t", " ")
    for _ in range(3):
        previous = cleaned
        cleaned = re.sub(r"\\u([0-9a-fA-F]{4})", _decode_unicode_escape, cleaned)
        cleaned = re.sub(r"\\x([0-9a-fA-F]{2})", _decode_hex_escape, cleaned)
        cleaned = re.sub(r"(?<!\\)\bu([0-9a-fA-F]{4})(?![0-9a-fA-F])", _decode_unicode_escape, cleaned)
        cleaned = cleaned.replace("\\\\", "\\")
        if cleaned == previous:
            break
    # Some scraped values arrive split like "Est u00E1ndar" -> "Est ándar".
    cleaned = re.sub(r"(?<=[A-Za-z])\s+(?=[^\x00-\x7F])", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    
    # Fix surrogate pairs (like emojis) that break python-telegram-bot
    try:
        cleaned = cleaned.encode('utf-16', 'surrogatepass').decode('utf-16')
    except Exception:
        pass
        
    return cleaned or None

def normalize_phone_number(value, country_code=None):
    cleaned = decode_netflix_value(value)
    if not cleaned:
        return None

    if str(cleaned).startswith("+"):
        return cleaned

    digits = re.sub(r"\D+", "", str(cleaned))
    if not digits:
        return cleaned

    normalized_country = (decode_netflix_value(country_code) or "").strip().upper()
    dial_prefix_map = {
        "IN": "91",
    }
    dial_prefix = dial_prefix_map.get(normalized_country)
    if dial_prefix and digits.startswith("0") and len(digits) >= 10:
        return f"+{dial_prefix}{digits.lstrip('0')}"

    return cleaned


def extract_first_match(response_text, patterns, flags=0):
    for pattern in patterns:
        match = re.search(pattern, response_text, flags)
        if match:
            return decode_netflix_value(match.group(1))
    return None


def parse_boolean_value(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value == 1:
            return True
        if value == 0:
            return False
        return None
    if isinstance(value, dict):
        for key in (
            "value",
            "isUserOnHold",
            "holdStatus",
            "isOnHold",
            "pastDue",
            "isPastDue",
            "isVerified",
            "verified",
        ):
            if key in value:
                parsed = parse_boolean_value(value.get(key))
                if parsed is not None:
                    return parsed
        return None
    cleaned = decode_netflix_value(value)
    if cleaned is None:
        return None
    lowered = str(cleaned).strip().lower()
    truthy = {"true", "yes", "1", "on"}
    falsy = {"false", "no", "0", "off"}
    if lowered in truthy:
        return True
    if lowered in falsy:
        return False
    return None


def format_boolean_label(value):
    parsed = parse_boolean_value(value)
    if parsed is True:
        return "Yes"
    if parsed is False:
        return "No"
    return None


def extract_bool_value(response_text, patterns):
    value = extract_first_match(response_text, patterns, re.IGNORECASE)
    if value is None:
        return None
    parsed = format_boolean_label(value)
    if parsed is not None:
        return parsed
    return value


def extract_profile_names(response_text):
    names = []
    for pattern in [
        r'"profileName"\s*:\s*"([^"]+)"',
        r'"profileName"\s*:\s*\{\s*"fieldType"\s*:\s*"String"\s*,\s*"value"\s*:\s*"([^"]+)"',
    ]:
        for found in re.findall(pattern, response_text, re.DOTALL):
            decoded = decode_netflix_value(found)
            if decoded and decoded not in names:
                names.append(decoded)
    for match in re.finditer(r'"__typename"\s*:\s*"Profile"', response_text):
        snippet = response_text[match.start():match.start() + 1200]
        name_match = re.search(r'"name"\s*:\s*"([^"]+)"', snippet)
        if name_match:
            decoded = decode_netflix_value(name_match.group(1))
            if decoded and decoded not in names:
                names.append(decoded)
    if not names:
        return None
    return ", ".join(names)


def merge_info(primary, fallback):
    merged = dict(fallback or {})
    for key, value in (primary or {}).items():
        if value not in (None, "", [], {}):
            merged[key] = value
    return merged


def has_complete_account_info(info):
    if not info:
        return False
    required_fields = (
        "countryOfSignup",
        "membershipStatus",
        "localizedPlanName",
        "maxStreams",
        "videoQuality",
    )
    return all(info.get(field) and info.get(field) != "null" for field in required_fields)


def extract_info_from_graphql_payload(response_text):
    try:
        payload = json.loads(response_text)
    except Exception:
        return {}

    if not isinstance(payload, dict):
        return {}

    data = payload.get("data")
    if not isinstance(data, dict):
        return {}

    growth_account = data.get("growthAccount") or {}
    current_profile = data.get("currentProfile") or {}
    current_plan = ((growth_account.get("currentPlan") or {}).get("plan") or {})
    next_plan = ((growth_account.get("nextPlan") or {}).get("plan") or {})
    next_billing = growth_account.get("nextBillingDate") or {}
    hold_meta = growth_account.get("growthHoldMetadata") or {}
    local_phone = growth_account.get("growthLocalizablePhoneNumber") or {}
    raw_phone = local_phone.get("rawPhoneNumber") or {}
    payment_methods = growth_account.get("growthPaymentMethods") or []
    payment_method = payment_methods[0] if payment_methods and isinstance(payment_methods[0], dict) else {}
    payment_logo = (payment_method.get("paymentOptionLogo") or {}).get("paymentOptionLogo")
    payment_typename = str(payment_method.get("__typename") or "")
    payment_display_text = decode_netflix_value(payment_method.get("displayText"))
    profiles = growth_account.get("profiles") or []
    phone_digits = None
    phone_verified_graphql = None
    phone_country_code = None
    if isinstance(raw_phone, dict):
        phone_digits_obj = raw_phone.get("phoneNumberDigits") or {}
        phone_digits = phone_digits_obj.get("value") if isinstance(phone_digits_obj, dict) else raw_phone.get("phoneNumberDigits")
        phone_verified_graphql = raw_phone.get("isVerified")
        phone_country_code = raw_phone.get("countryCode")
    else:
        phone_digits = raw_phone

    def _growth_email(profile_obj):
        if not isinstance(profile_obj, dict):
            return None, None
        growth_email = profile_obj.get("growthEmail") or {}
        email_obj = growth_email.get("email") or {}
        email_value = email_obj.get("value") if isinstance(email_obj, dict) else None
        return email_value, growth_email.get("isVerified")

    email_value, email_verified = _growth_email(current_profile)
    if not email_value:
        for profile in profiles:
            email_value, email_verified = _growth_email(profile)
            if email_value:
                break

    profile_names = []
    for profile in profiles:
        if isinstance(profile, dict):
            name = decode_netflix_value(profile.get("name"))
            if name and name not in profile_names:
                profile_names.append(name)

    feature_types = []
    for plan_obj in (current_plan, next_plan):
        for feature in (plan_obj.get("availableFeatures") or []):
            if isinstance(feature, dict) and feature.get("type"):
                feature_types.append(str(feature["type"]).upper())

    def _first_boolean_label(*candidates):
        for candidate in candidates:
            labeled = format_boolean_label(candidate)
            if labeled is not None:
                return labeled
        return None

    def _extract_price_value(plan_obj):
        if not isinstance(plan_obj, dict):
            return None
        direct_candidates = [
            plan_obj.get("priceDisplay"),
            plan_obj.get("displayPrice"),
            plan_obj.get("formattedPrice"),
            plan_obj.get("formattedPlanPrice"),
            plan_obj.get("planPriceDisplay"),
        ]
        for candidate in direct_candidates:
            decoded = decode_netflix_value(candidate)
            if decoded:
                return decoded

        price_obj = plan_obj.get("price")
        if isinstance(price_obj, dict):
            for key in ("displayValue", "formatted", "formattedPrice", "displayPrice", "value", "amountDisplay"):
                decoded = decode_netflix_value(price_obj.get(key))
                if decoded:
                    return decoded
        return None

    hold_status = _first_boolean_label(
        hold_meta.get("isUserOnHold") if isinstance(hold_meta, dict) else hold_meta,
        hold_meta.get("holdStatus") if isinstance(hold_meta, dict) else None,
        hold_meta.get("isOnHold") if isinstance(hold_meta, dict) else None,
        hold_meta.get("pastDue") if isinstance(hold_meta, dict) else None,
        growth_account.get("isUserOnHold"),
        growth_account.get("holdStatus"),
        growth_account.get("isOnHold"),
        growth_account.get("pastDue"),
        growth_account.get("isPastDue"),
    )

    info = {
        "accountOwnerName": decode_netflix_value(current_profile.get("name")),
        "email": decode_netflix_value(email_value),
        "countryOfSignup": decode_netflix_value(((growth_account.get("countryOfSignUp") or {}).get("code"))),
        "memberSince": decode_netflix_value(growth_account.get("memberSince")),
        "nextBillingDate": decode_netflix_value(next_billing.get("localDate") or next_billing.get("date")),
        "userGuid": decode_netflix_value(growth_account.get("ownerGuid") or current_profile.get("guid")),
        "showExtraMemberSection": "Yes" if "EXTRA_MEMBER" in feature_types else "No" if feature_types else None,
        "membershipStatus": decode_netflix_value(growth_account.get("membershipStatus")),
        "localizedPlanName": decode_netflix_value(current_plan.get("name") or next_plan.get("name")),
        "planPrice": _extract_price_value(current_plan) or _extract_price_value(next_plan),
        "paymentMethodType": decode_netflix_value(payment_logo or growth_account.get("payer")),
        "maskedCard": None,
        "phoneNumber": normalize_phone_number(phone_digits, phone_country_code),
        "videoQuality": decode_netflix_value(current_plan.get("videoQuality")),
        "holdStatus": hold_status,
        "emailVerified": format_boolean_label(email_verified),
        "phoneVerified": format_boolean_label(phone_verified_graphql),
        "profiles": ", ".join(profile_names) if profile_names else None,
    }

    if "Card" in payment_typename:
        info["paymentMethodType"] = "CC"
        if payment_display_text:
            if re.fullmatch(r"\d{4}", payment_display_text):
                info["maskedCard"] = payment_display_text
            else:
                info["maskedCard"] = payment_display_text
    elif payment_display_text and payment_logo is None and not re.fullmatch(r"\d{4}", payment_display_text):
        info["paymentMethodType"] = info["paymentMethodType"] or payment_display_text

    if not info["paymentMethodType"] and payment_methods:
        if "Card" in payment_typename:
            info["paymentMethodType"] = "CC"

    return {key: value for key, value in info.items() if value not in (None, "", [], {})}


def extract_info(response_text):
    graphql_info = extract_info_from_graphql_payload(response_text)
    extra_member_account_patterns = (
        r"assinante\s+extra\s+no\s+plano\s+de\s+outra\s+pessoa",
        r"suscriptor\s+extra\s+en\s+el\s+plan\s+de\s+otra\s+persona",
        r"extra\s+on\s+someone.?else.?s\s+plan",
        r"abbonato\s+extra\s+sul\s+piano\s+di\s+un.?altra\s+persona",
        r"abonn[ée]\s+suppl[ée]mentaire\s+sur\s+le\s+forfait\s+de\s+quelqu.?un\s+d.?autre",
        r"ekstra\s+uye\s+bir\s+baskasinin\s+planinda",
    )
    extra_member_by_response_text = any(
        re.search(pattern, response_text, re.IGNORECASE)
        for pattern in extra_member_account_patterns
    )
    if has_complete_account_info(graphql_info):
        # Fast path: GraphQL payload already has core account fields.
        extracted = dict(graphql_info)
    else:
        extracted = {
            "accountOwnerName": extract_first_match(
                response_text,
                [
                    r'userInfo"\s*:\s*\{\s*"name"\s*:\s*"([^"]+)"',
                    r'"accountOwnerName"\s*:\s*"([^"]+)"',
                    r'"name"\s*:\s*\{\s*"fieldType"\s*:\s*"String"\s*,\s*"value"\s*:\s*"([^"]+)"',
                    r'"firstName"\s*:\s*"([^"]+)"',
                ],
            ),
            "email": extract_first_match(
                response_text,
                [
                    r'"emailAddress"\s*:\s*"([^"]+)"',
                    r'"email"\s*:\s*"([^"]+)"',
                    r'"emailAddress"\s*:\s*"([^"]+)"',
                    r'"loginId"\s*:\s*"([^"]+)"',
                ],
            ),
            "countryOfSignup": extract_first_match(
                response_text,
                [
                    r'"currentCountry"\s*:\s*"([^"]+)"',
                    r'"countryOfSignup":\s*"([^"]+)"',
                ],
            ),
            "memberSince": extract_first_match(response_text, [
                r'"memberSince":\s*"([^"]+)"',
                r'data-uia="account-overview-page\+membership-card\+plan-banner"[^>]*>([^<]+)</div>',
                r'Assinante desde\s*([^<]+)',
                r'Member since\s*([^<]+)',
            ]),
            "nextBillingDate": extract_first_match(
                response_text,
                [
                    r'"GrowthNextBillingDate"\s*,\s*"date"\s*:\s*"([^"T]+)T',
                    r'"nextBillingDate"\s*:\s*"([^"]+)"',
                    r'"nextBilling"\s*:\s*\{\s*"fieldType"\s*:\s*"String"\s*,\s*"value"\s*:\s*"([^"]+)"',
                    r'data-uia="account-overview-page\+membership-card\+description"[^>]*>([^<]+)</p>',
                ],
            ),
            "userGuid": extract_first_match(response_text, [r'"userGuid":\s*"([^"]+)"']),
            "showExtraMemberSection": extract_bool_value(
                response_text,
                [
                    r'"showExtraMemberSection":\s*\{\s*"fieldType":\s*"Boolean",\s*"value":\s*(true|false)',
                    r'"showExtraMemberSection"\s*:\s*(true|false)',
                ],
            ),
            "membershipStatus": extract_first_match(response_text, [r'"membershipStatus":\s*"([^"]+)"']),
            "maxStreams": extract_first_match(
                response_text,
                [
                    r'maxStreams\":\{\"fieldType\":\"Numeric\",\"value\":([^,]+),',
                    r'"maxStreams"\s*:\s*"?([^",}]+)"?',
                ],
            ),
            "localizedPlanName": extract_first_match(
                response_text,
                [
                    r'"MemberPlan"\s*,\s*"fields"\s*:\s*\{\s*"localizedPlanName"\s*:\s*\{\s*"fieldType"\s*:\s*"String"\s*,\s*"value"\s*:\s*"([^"]+)"',
                    r'localizedPlanName\":\{\"fieldType\":\"String\",\"value\":\"([^"]+)"',
                    r'"currentPlan"\s*:\s*\{[\s\S]*?"plan"\s*:\s*\{[\s\S]*?"name"\s*:\s*"([^"]+)"',
                    r'"nextPlan"\s*:\s*\{[\s\S]*?"plan"\s*:\s*\{[\s\S]*?"name"\s*:\s*"([^"]+)"',
                    r'"localizedPlanName"\s*:\s*"([^"]+)"',
                    r'"planName"\s*:\s*"([^"]+)"',
                    r'data-uia="account-overview-page\+membership-card\+title"[^>]*>([^<]+)</h3>',
                ],
            ),
            "planPrice": extract_first_match(
                response_text,
                [
                    r'"formattedPlanPrice"\s*:\s*"([^"]+)"',
                    r'"formattedPrice"\s*:\s*"([^"]+)"',
                    r'"planPriceDisplay"\s*:\s*"([^"]+)"',
                    r'"displayPrice"\s*:\s*"([^"]+)"',
                    r'"planPrice"\s*:\s*\{[\s\S]*?"value"\s*:\s*"([^"]+)"',
                    r'"planPrice"[^}]+"value":"([^"]+)"',
                    r'planPrice[^}]+value[^}]+"([^"]+)"',
                    r'"price"\s*:\s*\{\s*"fieldType"\s*:\s*"String"\s*,\s*"value"\s*:\s*"([^"]+)"',
                    r'"planPrice"\s*:\s*"([^"]+)"',
                ],
            ),
            "paymentMethodExists": extract_bool_value(
                response_text,
                [
                    r'"paymentMethodExists":\s*\{\s*"fieldType":\s*"Boolean",\s*"value":\s*(true|false)',
                    r'"paymentMethodExists"\s*:\s*(true|false)',
                ],
            ),
            "paymentMethodType": extract_first_match(
                response_text,
                [
                    r'"paymentMethod"\s*:\s*\{\s*"fieldType"\s*:\s*"String"\s*,\s*"value"\s*:\s*"([^"]+)"',
                    r'"paymentMethod"\s*:\s*"([^"]+)"',
                    r'"paymentType"\s*:\s*"([^"]+)"',
                    r'"paymentMethodType"\s*:\s*"([^"]+)"',
                    r'data-uia="account-overview-page\+membership-card\+payment\+details-icon"[^>]*src="[^"]+/([^/.]+)\.png"',
                ],
            ),
            "maskedCard": extract_first_match(
                response_text,
                [
                    r'"__typename"\s*:\s*"GrowthCardPaymentMethod"[\s\S]*?"displayText"\s*:\s*"([^"]+)"',
                    r'"paymentCardDisplayString"\s*:\s*"([^"]+)"',
                    r'"paymentMethodLast4"\s*:\s*"([^"]+)"',
                    r'"paymentMethodLastFour"\s*:\s*"([^"]+)"',
                    r'"lastFour"\s*:\s*"([^"]+)"',
                    r'"creditCardLast4"\s*:\s*"([^"]+)"',
                    r'"creditCardEndingDigits"\s*:\s*"([^"]+)"',
                    r'"paymentMethodDescription"\s*:\s*"([^"]+)"',
                    r'"maskedCard"\s*:\s*"([^"]+)"',
                    r'"cardNumber"\s*:\s*"([^"]+)"',
                    r'data-uia="mopType"[^>]*>([^<]+)</span>',
                ],
            ),
            "phoneNumber": extract_first_match(
                response_text,
                [
                    r'"phoneNumberDigits"\s*:\s*\{[\s\S]*?"value"\s*:\s*"([^"]+)"',
                    r'"phoneNumber"\s*:\s*"([^"]+)"',
                    r'"mobilePhone"\s*:\s*"([^"]+)"',
                ],
            ),
            "phoneVerified": extract_bool_value(
                response_text,
                [
                    r'"phoneVerified"\s*:\s*(true|false)',
                    r'"isPhoneVerified"\s*:\s*(true|false)',
                ],
            ),
            "videoQuality": extract_first_match(
                response_text,
                [
                    r'videoQuality"\s*:\s*\{\s*"fieldType"\s*:\s*"String"\s*,\s*"value"\s*:\s*"([^"]+)"',
                    r'"videoQuality"\s*:\s*"([^"]+)"',
                    r'"quality"\s*:\s*"([^"]+)"',
                ],
            ),
            "holdStatus": extract_bool_value(
                response_text,
                [
                    r'"holdStatus"\s*:\s*(true|false)',
                    r'"holdStatus"\s*:\s*\{\s*"fieldType"\s*:\s*"Boolean"\s*,\s*"value"\s*:\s*(true|false)',
                    r'"isUserOnHold"\s*:\s*(true|false)',
                    r'"isUserOnHold"\s*:\s*\{\s*"fieldType"\s*:\s*"Boolean"\s*,\s*"value"\s*:\s*(true|false)',
                    r'"isOnHold"\s*:\s*(true|false)',
                    r'"pastDue"\s*:\s*(true|false)',
                    r'"isPastDue"\s*:\s*(true|false)',
                ],
            ),
            "emailVerified": extract_bool_value(
                response_text,
                [
                    r'"emailVerified"\s*:\s*(true|false)',
                    r'"isEmailVerified"\s*:\s*(true|false)',
                    r'"emailAddressVerified"\s*:\s*(true|false)',
                    r'"contactEmailVerified"\s*:\s*(true|false)',
                ],
            ),
            "profiles": extract_profile_names(response_text),
        }

        extracted = merge_info(graphql_info, extracted)

    extracted.setdefault("paymentMethodType", None)
    extracted.setdefault("paymentMethodExists", None)
    extracted.setdefault("maskedCard", None)
    extracted.setdefault("holdStatus", None)
    extracted.setdefault("emailVerified", None)
    extracted.setdefault("phoneNumber", None)
    extracted.setdefault("countryOfSignup", None)
    extracted.setdefault("membershipStatus", None)
    extracted.setdefault("localizedPlanName", None)

    if extra_member_by_response_text:
        extracted["isExtraMemberAccount"] = "Yes"

    extracted["localizedPlanName"] = (
        extracted["localizedPlanName"].replace("miembro u00A0extra", "(Extra Member)")
        if extracted["localizedPlanName"]
        else None
    )

    if not extracted["paymentMethodType"]:
        extracted["paymentMethodType"] = extracted["paymentMethodExists"]

    if extracted["maskedCard"] and re.fullmatch(r"\d{4}", extracted["maskedCard"]):
        if extracted.get("paymentMethodType") in {None, "", "Yes"}:
            extracted["paymentMethodType"] = "CC"

    if extracted["holdStatus"] is None:
        membership_status_key = normalize_plan_key(extracted.get("membershipStatus"))
        if membership_status_key == "current_member":
            extracted["holdStatus"] = "No"
        elif any(token in membership_status_key for token in ("hold", "past_due", "payment_retry", "paused", "suspend")):
            extracted["holdStatus"] = "Yes"

    if extracted["emailVerified"] is None and extracted.get("email"):
        extracted["emailVerified"] = "Yes"

    phone_number = extracted.get("phoneNumber")
    extracted["phoneDisplay"] = normalize_phone_number(phone_number, extracted.get("countryOfSignup"))

    profiles = extracted.get("profiles")
    if profiles:
        profile_count = len([name for name in profiles.split(", ") if name])
        extracted["profileCount"] = profile_count
        extracted["profilesDisplay"] = profiles
    else:
        extracted["profileCount"] = None
        extracted["profilesDisplay"] = None

    return translate_to_english(extracted)

def extract_security_info(response_text):
    info = {}
    
    # Extract Email
    email_block = re.search(
        r'data-uia="account-security-page\+account-details-card\+email-button(?:\+item\+label)?".*?(?:</button>|</a>|</li>)',
        response_text,
        re.DOTALL | re.IGNORECASE,
    )
    if email_block:
        block_text = email_block.group(0)
        email_match = re.search(
            r'<p[^>]*>\s*([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})\s*</p>',
            block_text,
            re.IGNORECASE,
        )
        if email_match:
            info['email'] = email_match.group(1).strip()

    if 'email' not in info:
        email_match = re.search(
            r'data-uia="account-security-page\+account-details-card\+email-button[^\"]*"[^>]*>.*?([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})',
            response_text,
            re.DOTALL | re.IGNORECASE,
        )
        if email_match:
            info['email'] = email_match.group(1).strip()
        
    # Extract Email Verified Status
    email_block = re.search(r'data-uia="account-security-page\+account-details-card\+email-button\+item\+label".*?(?:</li>|</button>|</a>)', response_text, re.DOTALL | re.IGNORECASE)
    if email_block:
        block_text = email_block.group(0).lower()
        if 'circleexclamationpoint' in block_text or 'requer verifi' in block_text or 'unverified' in block_text or 'verify your' in block_text or 'needs verification' in block_text:
            info['emailVerified'] = 'No'
        else:
            info['emailVerified'] = 'Yes'

    # Extract Phone
    phone_match = re.search(r'data-uia="account-security-page\+account-details-card\+phone\+item\+label"[^>]*>.*?</div>\s*<p[^>]*>([^<]+)</p>', response_text, re.DOTALL)
    if phone_match:
        info['phoneNumber'] = phone_match.group(1).strip()
        
    # Extract Phone Verified Status
    phone_block = re.search(r'data-uia="account-security-page\+account-details-card\+phone\+item\+label".*?(?:</li>|</button>|</a>)', response_text, re.DOTALL)
    if phone_block:
        block_text = phone_block.group(0).lower()
        if 'circleexclamationpoint' in block_text or 'requer verifi' in block_text or 'unverified' in block_text or 'verify your' in block_text or 'banner+verifyphone' in response_text.lower() or 'needs verification' in block_text:
            info['phoneVerified'] = 'No'
        else:
            info['phoneVerified'] = 'Yes'
            
    return {k: v for k, v in info.items() if v}

def extract_profiles_info(response_text):
    info = {}
    
    # Extract profiles from aria-label or specific data-uia label divs
    profile_matches = re.findall(r'<button[^>]*aria-label="([^"]+)"[^>]*data-cl-view="accountProfileSettings"', response_text)
    
    # Powerful fallback: extract from the text inside the label div
    if not profile_matches:
        profile_matches = re.findall(r'data-uia="menu-card\+account-profiles-page\+profiles-menu-card\+[^"]+\+item\+label"[^>]*>([^<]+)</div>', response_text)
        
    if not profile_matches:
        profile_matches = re.findall(r'data-uia="menu-card\+account-profiles-page\+profiles-menu-card\+([^+"]+)(?:\+item)?"', response_text)
    
    # Deduplicate while preserving order
    unique_profiles = []
    for p in profile_matches:
        cleaned = p.strip()
        if cleaned and cleaned not in unique_profiles:
            unique_profiles.append(cleaned)
            
    if unique_profiles:
        info['profiles'] = ", ".join(unique_profiles)
        info['profileCount'] = len(unique_profiles)
        info['profilesDisplay'] = info['profiles']
        
    # Extract Extra Members if present
    # Looking for extra members section or similar text
    extra_member_patterns = [
        r'assinante\s+extra', r'suscriptor\s+extra', r'extra\s+member', 
        r'abbonato\s+extra', r'abonn[ée]\s+suppl[ée]mentaire'
    ]
    if any(re.search(pattern, response_text, re.IGNORECASE) for pattern in extra_member_patterns):
        info['isExtraMemberAccount'] = 'Yes'
        
    return info

def translate_date_to_english(date_str):
    if not date_str:
        return date_str
        
    date_str = str(date_str).lower()
    
    prefixes = ['próximo pagamento:', 'proximo pago:', 'next billing date:', 'next payment:', 'pagamento:', 'pago:']
    for p in prefixes:
        if date_str.startswith(p):
            date_str = date_str.replace(p, '').strip()
            
    months_map = {
        'january': 'January', 'janeiro': 'January', 'janvier': 'January', 'enero': 'January', 'jan': 'January',
        'february': 'February', 'fevereiro': 'February', 'février': 'February', 'febrero': 'February', 'feb': 'February',
        'march': 'March', 'março': 'March', 'mars': 'March', 'marzo': 'March', 'mar': 'March',
        'april': 'April', 'abril': 'April', 'avril': 'April', 'apr': 'April',
        'may': 'May', 'maio': 'May', 'mai': 'May', 'mayo': 'May',
        'june': 'June', 'junho': 'June', 'juin': 'June', 'junio': 'June', 'jun': 'June',
        'july': 'July', 'julho': 'July', 'juillet': 'July', 'julio': 'July', 'jul': 'July',
        'august': 'August', 'agosto': 'August', 'août': 'August', 'aug': 'August',
        'september': 'September', 'setembro': 'September', 'septembre': 'September', 'septiembre': 'September', 'sep': 'September',
        'october': 'October', 'outubro': 'October', 'octobre': 'October', 'octubre': 'October', 'oct': 'October',
        'november': 'November', 'novembro': 'November', 'novembre': 'November', 'nov': 'November',
        'december': 'December', 'dezembro': 'December', 'décembre': 'December', 'diciembre': 'December', 'dec': 'December'
    }
    
    month_keys = sorted(months_map.keys(), key=len, reverse=True)
    pattern = r'(' + '|'.join(month_keys) + r')'
    
    month_match = re.search(pattern, date_str, re.IGNORECASE)
    year_match = re.search(r'(\d{4})', date_str)
    day_match = re.search(r'\b(\d{1,2})\b(?!\d{2})', date_str)
    
    if month_match and year_match:
        eng_month = months_map[month_match.group(1).lower()]
        year = year_match.group(1)
        if day_match:
            return f'{eng_month} {day_match.group(1)}, {year}'
        else:
            return f'{eng_month} {year}'
            
    return date_str.title()

def country_code_to_flag(country_code):
    if not country_code: return ""
    upper = country_code.upper()
    if len(upper) == 2 and upper.isalpha():
        return "".join(chr(127397 + ord(char)) for char in upper)
    return ""

def get_full_country_name(code):
    mapping = {
        "US": "United States", "BR": "Brazil", "IN": "India", "GB": "United Kingdom",
        "PH": "Philippines", "CA": "Canada", "AU": "Australia", "DE": "Germany",
        "FR": "France", "IT": "Italy", "ES": "Spain", "MX": "Mexico", "AR": "Argentina",
        "CO": "Colombia", "CL": "Chile", "PE": "Peru", "JP": "Japan", "KR": "South Korea",
        "ID": "Indonesia", "MY": "Malaysia", "SG": "Singapore", "TH": "Thailand",
        "VN": "Vietnam", "AE": "United Arab Emirates", "SA": "Saudi Arabia", "TR": "Turkey",
        "ZA": "South Africa", "NL": "Netherlands", "SE": "Sweden", "CH": "Switzerland",
        "PL": "Poland", "RO": "Romania", "GR": "Greece", "PT": "Portugal", "IE": "Ireland"
    }
    return mapping.get(code.upper(), code.upper())

def translate_to_english(extracted_info):
    country = extracted_info.get("countryOfSignup")
    if country:
        flag = country_code_to_flag(country)
        full_name = get_full_country_name(country)
        extracted_info["countryOfSignup"] = f"{full_name} {flag}".strip()

    plan_name = extracted_info.get("localizedPlanName")
    if plan_name:
        plan_key = normalize_plan_key(plan_name)
        if "premium" in plan_key or "ultra" in plan_key:
            extracted_info["localizedPlanName"] = "Premium"
        elif "ad" in plan_key or "anuncio" in plan_key or "pub" in plan_key or "reklam" in plan_key:
            if "basic" in plan_key or "basico" in plan_key:
                extracted_info["localizedPlanName"] = "Basic with Ads"
            else:
                extracted_info["localizedPlanName"] = "Standard with Ads"
        elif "standard" in plan_key or "padrao" in plan_key or "estandar" in plan_key or "standart" in plan_key:
            extracted_info["localizedPlanName"] = "Standard"
        elif "basic" in plan_key or "basico" in plan_key:
            extracted_info["localizedPlanName"] = "Basic"
        elif "mobile" in plan_key or "movil" in plan_key:
            extracted_info["localizedPlanName"] = "Mobile"
            
    # Translate dates
    member_since = extracted_info.get("memberSince")
    if member_since:
        extracted_info["memberSince"] = translate_date_to_english(member_since)
        
    next_billing = extracted_info.get("nextBillingDate")
    if next_billing:
        extracted_info["nextBillingDate"] = translate_date_to_english(next_billing)
            
    status = extracted_info.get("membershipStatus")
    if status:
        status_key = normalize_plan_key(status)
        if status_key == "current_member":
            extracted_info["membershipStatus"] = "Active"
        elif any(x in status_key for x in ["cancel", "former"]):
            extracted_info["membershipStatus"] = "Cancelled"
        else:
            extracted_info["membershipStatus"] = "Active"

    return extracted_info

def normalize_plan_key(plan_name):
    if not plan_name:
        return "unknown"
    simplified = unicodedata.normalize("NFKD", plan_name)
    simplified = "".join(ch for ch in simplified if not unicodedata.combining(ch))
    normalized = re.sub(r"[^\w]+", "_", simplified.lower(), flags=re.UNICODE).strip("_")
    return normalized or "unknown"


def format_plan_label(plan_key):
    if not plan_key:
        return "Unknown"
    label = plan_key.replace("_", " ").strip()
    return label.title() if label else "Unknown"


