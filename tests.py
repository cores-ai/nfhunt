import asyncio
import re
import sys
import os

sys.path.append(r'C:\Users\iq4u8\Desktop\NF-Filter')
try:
    import scraper
    from bot import parse_netflix_cookies
    from fast_extractor import extract_first_match, translate_to_english
except ImportError:
    pass

def run_test_api():
    print(f'\n--- Running { "run_test_api" } ---')
    
    sys.path.append(r'C:\Users\iq4u8\Desktop\NF-Filter')
    
    async def test():
        cookie_str = "NetflixId=ct%3DBgjHlOvcAxK6A3RdLL4LRTcFWW_BhilhEV_ZzYp0DrvDO_AluUmgJqyj8tMTu5VUZBmixfNLcRVGGGUnMwErYfK9VPYf5vBHDigep8O19Y9BCyCahOQAacyAr9DN1ILZJwn1a7UGhpuOiUvxXSIOOUbpVtxcOgWvGFLmIMO2YEYd1YLN9H90-V9FEAqO7eKjM3tJmWOug-DOhqLiWi3aNaTyfhYLZfYPbDY9yjRThfw9JjQswJzupo0RDqeQGJGjm3Yqye6yCbaDxVsZls029XxlLUS6owW_EzW7sdVqC1ecGJ82mZ8jo2bf510AZTLaQeGljvXVO3wPbDhkK3_6UPjqO4Qt2boQ-ZDxXK5--UjASu6nBNolkEwGmDcftx7WUVTqVdBC6xxxCEOiDEK7TDMLpM5V89D9qhitvT7oQhao0zI6R6ce7R4ebCm1S_zYKOEd_6kduNkiGiuQSayXXZZ6XScx2VUBlkDP8u8oJ8Ew4iEuyHVc-BzN7FaApUZiBW2XM9bOlmnqPi7tpaNuBkzWiljkV6GwXmZU8kqoCtMrAcmXNnFgH6XXiyqYZe5ICwcKBFthZdEWD-_vnhi7OSx54kEYBiIOCgzJTUiwaEnybtEI6cM.%26ch%3DAQEAEAABABQLcJdPfPrKQ8FZNUm2_qLCk1vT_NHEbgM.%26v%3D3%26pg%3DBMM5GS3KWZEPTA3KTF2QC6ULJI"
        cookies = parse_netflix_cookies(cookie_str)
        print("Parsed cookies:", cookies)
        link = await scraper.generate_direct_login_link_api(cookies)
        print("Link:", link)
    
    asyncio.run(test())
    

def run_test_plan():
    print(f'\n--- Running { "run_test_plan" } ---')
    
    html_plan = '''<div class="default-ltr-iqcdef-cache-h12ju7 e1nu0bc05"><div data-uia="account-overview-page+membership-card+plan-banner" class="eh8zzqc3 default-ltr-iqcdef-cache-1jg1ig4 e12qwpva0">Assinante desde janeiro de 2023</div><div class="default-ltr-iqcdef-cache-15x2jb3 e1nu0bc03"><h3 data-uia="account-overview-page+membership-card+title" class="default-ltr-iqcdef-cache-t91nwz e1nu0bc02">Plano Padrão com anúncios</h3><p id="" class="default-ltr-iqcdef-cache-1g0dqi e1nu0bc00" data-uia="account-overview-page+membership-card+description">Próximo pagamento: 30 de julho de 2026</p>'''
    
    patterns_plan = [
        r'data-uia="plan-label">([^<]+)</b>',
        r'data-uia="plan-label"[^>]*>([^<]+)',
        r'data-uia="account-overview-page\+membership-card\+title"[^>]*>([^<]+)'
    ]
    match_plan = extract_first_match(html_plan, patterns_plan)
    print('Plan extracted:', match_plan)
    
    patterns_date = [
        r'data-uia="account-overview-page\+membership-card\+description"[^>]*>([^<]+)'
    ]
    match_date = extract_first_match(html_plan, patterns_date)
    print('Date extracted:', match_date)
    
    patterns_member = [
        r'data-uia="account-overview-page\+membership-card\+plan-banner"[^>]*>Assinante desde\s*([^<]+)',
        r'data-uia="account-overview-page\+membership-card\+plan-banner"[^>]*>Member Since\s*([^<]+)',
        r'data-uia="account-overview-page\+membership-card\+plan-banner"[^>]*>(?:[^<]*?desde|[^<]*?since)\s*([^<]+)'
    ]
    match_member = extract_first_match(html_plan, patterns_member)
    print('Member extracted:', match_member)
    
    info = {
        'localizedPlanName': match_plan,
        'nextBillingDate': match_date,
        'memberSince': match_member
    }
    info = translate_to_english(info)
    print('Translated Info:', info)
    

def run_test_email():
    print(f'\n--- Running { "run_test_email" } ---')
    
    html = '''<button aria-label="Email" data-uia="account-security-page+account-details-card+email-button" data-cl-view="verifyEmail" class="eawouyh0 default-ltr-iqcdef-cache-xdk3g" type="button"><div class="default-ltr-iqcdef-cache-qdaa6j"><div class="default-ltr-iqcdef-cache-7elf7u ez4ssc21"><svg viewBox="0 0 24 24" width="24" height="24" data-icon="EnvelopeMedium" data-icon-id=":Rkdaqjla9lalalal6:" aria-hidden="true" xmlns="http://www.w3.org/2000/svg" fill="none" role="img"><path fill="currentColor" fill-rule="evenodd" d="M2 3a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h20a2 2 0 0 0 2-2V5a2 2 0 0 0-2-2zm0 3.221V19h20V6.221q-.106.135-.241.259L20.413 5H3.587L12 12.649 20.413 5l1.346 1.48-9.086 8.26-.673.611-.673-.611-9.086-8.26A2 2 0 0 1 2 6.22" clip-rule="evenodd"></path></svg></div></div><div class="default-ltr-iqcdef-cache-1la96xq"><div data-uia="account-security-page+account-details-card+email-button+item+label" id=":Rajla9lalalal6:" class="default-ltr-iqcdef-cache-1mgbvf4">Email</div><p class="default-ltr-iqcdef-cache-11mfvgc">millenavitoria0107@gmail.com</p><p class="default-ltr-iqcdef-cache-r98rqt"><span data-hawkins-id="CircleExclamationPoint" aria-hidden="true" style="--wct--icon--mask-default:var(--wct--icon--mask-small);--wct--icon--mask-extra-large:url(https://occ.a.nflxso.net/dnmt/api/v6/jXBzyCe8ipOg2XQkxoKMq9wr74g/AAAAAeBy03shTjdmMTOc2YffMKETsjJus3c09yCo-5yiThyLAFS_OSgs0jhiyg-5HrPaBfqMNGz5ZX4ukPeqEHZiY1a4tGzoDEtDz2NfwW9qrViyW1XIBCUXGEwJpbl53X0pH3BJXddQD_RL6zWLwBUWCHsySTbePN64ZLcB.svg);--wct--icon--mask-large:url(https://occ.a.nflxso.net/dnmt/api/v6/jXBzyCe8ipOg2XQkxoKMq9wr74g/AAAAATtR74phNReBqEokpMFNLRKHlR-e-6Atk_mgxBjUayw02t1_E31HU7JHxqSMmaQQdykD5hgMA37N8WrpyZmA6fdi60m3VC1SjVZ5lvceag4KonsanDOmlshS2LJGzSGLibItca5YXAymxmA566bgbdntG6Y.svg);--wct--icon--mask-medium:url(https://occ.a.nflxso.net/dnmt/api/v6/jXBzyCe8ipOg2XQkxoKMq9wr74g/AAAAAULwMvhNQ2z5tqnJDhIfqKtmAYRWJmZBft4-NoUiPEJLDrjOsWbl4mFaRrPSFvkApDhbhBvGOILKXQwC46V4hXM7atQDlhIbuwatnvmrUpayNpoVkp4yjA2C8nmTRsFKs2A3FJHeb-kiLe5_UTOUCuHNltzJ.svg);--wct--icon--mask-small:url(https://occ.a.nflxso.net/dnmt/api/v6/jXBzyCe8ipOg2XQkxoKMq9wr74g/AAAAAXS5gknooxMgrh_CpY0FaBw-ZwIlJObWqkMMeXs9jx_37FRIGp60RYuyDvOQUU24uECNXxn1t6BWsvT4eIju_tsGSqDhxrGPXoIjbsZEgdh7DQ0B9xCYBDlxDIVbu_Scg1FkJXutNJAwbrg1-LaOmB6e2lY.svg)" role="img" class="default-ltr-iqcdef-cache-se5fp efb0ppv0"></span>Requer verificação</p></div><div class="default-ltr-iqcdef-cache-4kzo17"></div><div class="default-ltr-iqcdef-cache-rmc6oh"><div class="default-ltr-iqcdef-cache-1r684zb"><span data-hawkins-id="ChevronRight" aria-hidden="true" style="--wct--icon--mask-default:var(--wct--icon--mask-small);--wct--icon--mask-extra-large:url(https://occ.a.nflxso.net/dnmt/api/v6/jXBzyCe8ipOg2XQkxoKMq9wr74g/AAAAAfHHQag5eO6u7CL_eogZnSdAKreNVnRyj02IkOGaoM-vgsFGlmfm7V91HZdWDQYnnGU_RhNoJdoVyPPuTVFLvX_t2TcP36MCpiQvLcVzWsIXPKvXbFJoxau_A3qTXZcrq6AtoG_ICzxPDa9OxRkQ.svg);--wct--icon--mask-large:url(https://occ.a.nflxso.net/dnmt/api/v6/jXBzyCe8ipOg2XQkxoKMq9wr74g/AAAAATIF1xc6vyxc2J4ToNpBd3Xx6genzvGLZ2p6TFZi8oUuppVez8X8RAKQaQf4TMo-AzIJUhaNWSwWcrIqwdF5lAaMFPUQ73s3A0QUKsT8MflnAQr4TERpD2q7hAI8BjwyxnY6tZ3zFjCz.svg);--wct--icon--mask-medium:url(https://occ.a.nflxso.net/dnmt/api/v6/jXBzyCe8ipOg2XQkxoKMq9wr74g/AAAAAVeCx4aX8bz7ZlEIDWh0KLDyUpRw4Xxdl-od4aj38KQBbj1sWAjTJZ4ItnGot6FPcUOAfpnhVPRrcqXQpt9p9soKQtmoSmXQX64D9lohI9-qsMG_TH1NZQ_JuKVOjU_aNj-zD6mb-Ki2kQ.svg);--wct--icon--mask-small:url(https://occ.a.nflxso.net/dnmt/api/v6/jXBzyCe8ipOg2XQkxoKMq9wr74g/AAAAAYAtYoXZ6JnBp1tQ8YOl1pFQwHdN7RbYvwlT7lEqIxMZYKsGmokZ-20lJx0SEu8SaUmA9iCBmGVKQ6TAkxHmYHU-MShD_tbpriLP6ShmG25A93_O6wmFP0qCQPPXEMu5xq4Qr72YTkqa.svg)" role="img" dir="inherit" class="default-ltr-iqcdef-cache-se5fp efb0ppv0"></span></div></div></button>'''
    
    email_block = re.search(r'data-uia="account-security-page\+account-details-card\+email-button\+item\+label".*?(?:</li>|</button>|</a>)', html, re.DOTALL)
    if email_block:
        print('Block found')
        block_text = email_block.group(0).lower()
        if 'requer' in block_text or 'verify' in block_text or 'exclamation' in block_text or 'unverified' in block_text:
            print('Verified: No')
        else:
            print('Verified: Yes')
    else:
        print('Block NOT found')
    

def run_test_profile():
    print(f'\n--- Running { "run_test_profile" } ---')
    
    html = '''<li data-uia="menu-card+account-profiles-page+profiles-menu-card+M👾+item" class="default-ltr-iqcdef-cache-bh496p e13g7das1"><button aria-label="M👾" data-cl-view="accountProfileSettings" data-uia="menu-card+account-profiles-page+profiles-menu-card+M👾" class="eawouyh0 default-ltr-iqcdef-cache-xdk3g" type="button"><div class="default-ltr-iqcdef-cache-qdaa6j"><div class="default-ltr-iqcdef-cache-7elf7u ez4ssc21"><img alt="" src="https://occ-0-3776-2186.1.nflxso.net/dnm/api/v6/SO2HoVCx33X8phZh2pZZmQ4QgNY/AAAABSZiVkL3xitVHy4k4SmBTHVZH_ovC6OIqKQeYIQDy5DKYoKIDAEPPpsPf80DtTsL--iL9KbmcMStwV40YFD_Eu2CGFyTVXoIbg.png?r=368" data-uia="https://occ-0-3776-2186.1.nflxso.net/dnm/api/v6/SO2HoVCx33X8phZh2pZZmQ4QgNY/AAAABSZiVkL3xitVHy4k4SmBTHVZH_ovC6OIqKQeYIQDy5DKYoKIDAEPPpsPf80DtTsL--iL9KbmcMStwV40YFD_Eu2CGFyTVXoIbg.png?r=368+avatar" shape="square" class="default-ltr-iqcdef-cache-3d08xv e1jwi9xp1"></div></div><div class="default-ltr-iqcdef-cache-1la96xq"><div data-uia="menu-card+account-profiles-page+profiles-menu-card+M👾+item+label" id=":ra:" class="default-ltr-iqcdef-cache-1mgbvf4">M👾</div></div><div class="default-ltr-iqcdef-cache-4kzo17"><span class="default-ltr-iqcdef-cache-esr26m epk3ion0">Now Watching</span></div><div class="default-ltr-iqcdef-cache-rmc6oh"><div class="default-ltr-iqcdef-cache-1r684zb"><span data-hawkins-id="ChevronRight" aria-hidden="true" role="img" dir="inherit" class="default-ltr-iqcdef-cache-se5fp efb0ppv0" style="--wct--icon--mask-default: var(--wct--icon--mask-small); --wct--icon--mask-extra-large: url(https://occ.a.nflxso.net/dnmt/api/v6/jXBzyCe8ipOg2XQkxoKMq9wr74g/AAAAAfHHQag5eO6u7CL_eogZnSdAKreNVnRyj02IkOGaoM-vgsFGlmfm7V91HZdWDQYnnGU_RhNoJdoVyPPuTVFLvX_t2TcP36MCpiQvLcVzWsIXPKvXbFJoxau_A3qTXZcrq6AtoG_ICzxPDa9OxRkQ.svg); --wct--icon--mask-large: url(https://occ.a.nflxso.net/dnmt/api/v6/jXBzyCe8ipOg2XQkxoKMq9wr74g/AAAAATIF1xc6vyxc2J4ToNpBd3Xx6genzvGLZ2p6TFZi8oUuppVez8X8RAKQaQf4TMo-AzIJUhaNWSwWcrIqwdF5lAaMFPUQ73s3A0QUKsT8MflnAQr4TERpD2q7hAI8BjwyxnY6tZ3zFjCz.svg); --wct--icon--mask-medium: url(https://occ.a.nflxso.net/dnmt/api/v6/jXBzyCe8ipOg2XQkxoKMq9wr74g/AAAAAVeCx4aX8bz7ZlEIDWh0KLDyUpRw4Xxdl-od4aj38KQBbj1sWAjTJZ4ItnGot6FPcUOAfpnhVPRrcqXQpt9p9soKQtmoSmXQX64D9lohI9-qsMG_TH1NZQ_JuKVOjU_aNj-zD6mb-Ki2kQ.svg); --wct--icon--mask-small: url(https://occ.a.nflxso.net/dnmt/api/v6/jXBzyCe8ipOg2XQkxoKMq9wr74g/AAAAAYAtYoXZ6JnBp1tQ8YOl1pFQwHdN7RbYvwlT7lEqIxMZYKsGmokZ-20lJx0SEu8SaUmA9iCBmGVKQ6TAkxHmYHU-MShD_tbpriLP6ShmG25A93_O6wmFP0qCQPPXEMu5xq4Qr72YTkqa.svg);"></span></div></div></button></li>'''
    
    matches1 = re.findall(r'<button[^>]*aria-label="([^"]+)"[^>]*data-cl-view="accountProfileSettings"', html)
    matches2 = re.findall(r'data-uia="menu-card\+account-profiles-page\+profiles-menu-card\+([^+"]+)(?:\+item)?"', html)
    
    print("Matches 1:", matches1)
    print("Matches 2:", matches2)
    

def run_test_ver():
    print(f'\n--- Running { "run_test_ver" } ---')
    
    html = '''<button aria-label="Email" data-uia="account-security-page+account-details-card+email-button" data-cl-view="verifyEmail" class="eawouyh0 default-ltr-iqcdef-cache-xdk3g" type="button"><div class="default-ltr-iqcdef-cache-qdaa6j"><div class="default-ltr-iqcdef-cache-7elf7u ez4ssc21"><svg viewBox="0 0 24 24" width="24" height="24" data-icon="EnvelopeMedium" data-icon-id=":rt:" aria-hidden="true" xmlns="http://www.w3.org/2000/svg" fill="none" role="img"><path fill="currentColor" fill-rule="evenodd" d="M2 3a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h20a2 2 0 0 0 2-2V5a2 2 0 0 0-2-2zm0 3.221V19h20V6.221q-.106.135-.241.259L20.413 5H3.587L12 12.649 20.413 5l1.346 1.48-9.086 8.26-.673.611-.673-.611-9.086-8.26A2 2 0 0 1 2 6.22" clip-rule="evenodd"></path></svg></div></div><div class="default-ltr-iqcdef-cache-1la96xq"><div data-uia="account-security-page+account-details-card+email-button+item+label" id=":rs:" class="default-ltr-iqcdef-cache-1mgbvf4">Email</div><p class="default-ltr-iqcdef-cache-11mfvgc">millenavitoria0107@gmail.com</p><p class="default-ltr-iqcdef-cache-r98rqt"><span data-hawkins-id="CircleExclamationPoint" aria-hidden="true" role="img" class="default-ltr-iqcdef-cache-se5fp efb0ppv0" style="--wct--icon--mask-default: var(--wct--icon--mask-small); --wct--icon--mask-extra-large: url(https://occ.a.nflxso.net/dnmt/api/v6/jXBzyCe8ipOg2XQkxoKMq9wr74g/AAAAAeBy03shTjdmMTOc2YffMKETsjJus3c09yCo-5yiThyLAFS_OSgs0jhiyg-5HrPaBfqMNGz5ZX4ukPeqEHZiY1a4tGzoDEtDz2NfwW9qrViyW1XIBCUXGEwJpbl53X0pH3BJXddQD_RL6zWLwBUWCHsySTbePN64ZLcB.svg); --wct--icon--mask-large: url(https://occ.a.nflxso.net/dnmt/api/v6/jXBzyCe8ipOg2XQkxoKMq9wr74g/AAAAATtR74phNReBqEokpMFNLRKHlR-e-6Atk_mgxBjUayw02t1_E31HU7JHxqSMmaQQdykD5hgMA37N8WrpyZmA6fdi60m3VC1SjVZ5lvceag4KonsanDOmlshS2LJGzSGLibItca5YXAymxmA566bgbdntG6Y.svg); --wct--icon--mask-medium: url(https://occ.a.nflxso.net/dnmt/api/v6/jXBzyCe8ipOg2XQkxoKMq9wr74g/AAAAAULwMvhNQ2z5tqnJDhIfqKtmAYRWJmZBft4-NoUiPEJLDrjOsWbl4mFaRrPSFvkApDhbhBvGOILKXQwC46V4hXM7atQDlhIbuwatnvmrUpayNpoVkp4yjA2C8nmTRsFKs2A3FJHeb-kiLe5_UTOUCuHNltzJ.svg); --wct--icon--mask-small: url(https://occ.a.nflxso.net/dnmt/api/v6/jXBzyCe8ipOg2XQkxoKMq9wr74g/AAAAAXS5gknooxMgrh_CpY0FaBw-ZwIlJObWqkMMeXs9jx_37FRIGp60RYuyDvOQUU24uECNXxn1t6BWsvT4eIju_tsGSqDhxrGPXoIjbsZEgdh7DQ0B9xCYBDlxDIVbu_Scg1FkJXutNJAwbrg1-LaOmB6e2lY.svg);"></span>Needs verification</p></div><div class="default-ltr-iqcdef-cache-4kzo17"></div><div class="default-ltr-iqcdef-cache-rmc6oh"><div class="default-ltr-iqcdef-cache-1r684zb"><span data-hawkins-id="ChevronRight" aria-hidden="true" role="img" dir="inherit" class="default-ltr-iqcdef-cache-se5fp efb0ppv0" style="--wct--icon--mask-default: var(--wct--icon--mask-small); --wct--icon--mask-extra-large: url(https://occ.a.nflxso.net/dnmt/api/v6/jXBzyCe8ipOg2XQkxoKMq9wr74g/AAAAAfHHQag5eO6u7CL_eogZnSdAKreNVnRyj02IkOGaoM-vgsFGlmfm7V91HZdWDQYnnGU_RhNoJdoVyPPuTVFLvX_t2TcP36MCpiQvLcVzWsIXPKvXbFJoxau_A3qTXZcrq6AtoG_ICzxPDa9OxRkQ.svg); --wct--icon--mask-large: url(https://occ.a.nflxso.net/dnmt/api/v6/jXBzyCe8ipOg2XQkxoKMq9wr74g/AAAAATIF1xc6vyxc2J4ToNpBd3Xx6genzvGLZ2p6TFZi8oUuppVez8X8RAKQaQf4TMo-AzIJUhaNWSwWcrIqwdF5lAaMFPUQ73s3A0QUKsT8MflnAQr4TERpD2q7hAI8BjwyxnY6tZ3zFjCz.svg); --wct--icon--mask-medium: url(https://occ.a.nflxso.net/dnmt/api/v6/jXBzyCe8ipOg2XQkxoKMq9wr74g/AAAAAVeCx4aX8bz7ZlEIDWh0KLDyUpRw4Xxdl-od4aj38KQBbj1sWAjTJZ4ItnGot6FPcUOAfpnhVPRrcqXQpt9p9soKQtmoSmXQX64D9lohI9-qsMG_TH1NZQ_JuKVOjU_aNj-zD6mb-Ki2kQ.svg); --wct--icon--mask-small: url(https://occ.a.nflxso.net/dnmt/api/v6/jXBzyCe8ipOg2XQkxoKMq9wr74g/AAAAAYAtYoXZ6JnBp1tQ8YOl1pFQwHdN7RbYvwlT7lEqIxMZYKsGmokZ-20lJx0SEu8SaUmA9iCBmGVKQ6TAkxHmYHU-MShD_tbpriLP6ShmG25A93_O6wmFP0qCQPPXEMu5xq4Qr72YTkqa.svg);"></span></div></div></button>'''
    
    email_block = re.search(r'data-uia="account-security-page\+account-details-card\+email-button\+item\+label".*?(?:</li>|</button>|</a>)', html, re.DOTALL)
    if email_block:
        block_text = email_block.group(0).lower()
        print('Block text:', block_text[:100], '... length:', len(block_text))
        if 'circleexclamationpoint' in block_text or 'requer verifi' in block_text or 'unverified' in block_text or 'verify your' in block_text or 'needs verification' in block_text:
            print('Verified: No')
        else:
            print('Verified: Yes')
    else:
        print('Email block not found')
    

if __name__ == '__main__':
    # run_test_api()
    # run_test_plan()
    # run_test_email()
    # run_test_profile()
    # run_test_ver()
    print('Uncomment the test functions above to run them.')
