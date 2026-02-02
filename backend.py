# -*- coding: utf-8 -*-
import asyncio
import os
import re
import json
import sys
import time
import unicodedata
import codecs
import threading
from telethon import TelegramClient
from telethon.sessions import StringSession
from flask import Flask, request, jsonify, Response

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False

# stdout / stderr UTF-8 (best-effort)
try:
    if hasattr(sys.stdout, "fileno"):
        sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)
    if hasattr(sys.stderr, "fileno"):
        sys.stderr = open(sys.stderr.fileno(), mode='w', encoding='utf-8', buffering=1)
except Exception:
    pass

# ========== CONFIG ==========
API_ID = 17570480
API_HASH = '18c5be05094b146ef29b0cb6f6601f1f'
SESSION_STRING = "1ApWapzMBu0NYE8mBCNSqckiBtat2n26StfHX1k4VFOL9G547ShPWlgR1N6ysqX0JiTAMrpAtCzmpewdQab8RoYIU-6v6FV0j0NU3xtRdMUyvbpD5CkS3U_pKO08JWmzThQSIHkYG3gcIK8NvyCR1S9BzmUgsIxYcDU8sXVXnjD_E2CCSLVSY56rdleZYrYMScAeiSqnup-5IL1BPepP2eX8VcCvWyzFEn8C4tvbkIRGSpEEnlBwfsSE68LbR5HA0KAYRZwUekIPi0xy83CbQADQnlmIq0b-wZL91BRJ7heiMgifJHew_uk4d42Fa3wvigWn9_Q5kcc1dXcYJ4t4x0cY36RkKSB8="
BOT_USERNAME = "Miyavrem_bot"

# ========== GLOBALS ==========
client = None
loop = None
result_cache = {}
CACHE_TTL = 300  # cache time-to-live in seconds

# ========== HELPERS ==========
def fix_unicode_escapes(text: str) -> str:
    """
    Eğer metin içinde unicode-escape tarzı literal kaçışlar varsa (ör. backslash-uXXXX),
    birkaç güvenli decode denemesi yap.
    """
    if not text:
        return ""
    try:
        # Eğer string içinde gerçekten backslash + u gibi literaller varsa decode etmeye çalış
        if '\\u' in text or '\\x' in text or '\\U' in text:
            try:
                return codecs.decode(text, 'unicode_escape')
            except Exception:
                pass
            try:
                return codecs.decode(text, 'raw_unicode_escape')
            except Exception:
                pass
            try:
                return text.encode('utf-8', 'surrogatepass').decode('unicode_escape', 'ignore')
            except Exception:
                pass
    except Exception:
        pass
    return text

def _clean_control_chars(s: str) -> str:
    """
    Görünmez/kontrol karakterlerini güvenli şekilde temizle.
    """
    if not s:
        return s
    remove_cps = [0x200B, 0x200C, 0x200D, 0xFEFF, 0x00A0]
    remove_cps += list(range(0x202A, 0x202F))
    remove_cps += list(range(0x2066, 0x206A))
    for cp in remove_cps:
        ch = chr(cp)
        if ch in s:
            s = s.replace(ch, '')
    return s

def normalize_turkish_text(text: str) -> str:
    """
    Türkçe normalizasyon:
      - unicode-escape çözme
      - mojibake düzeltme
      - unicode normalizasyon
      - kontrol karakterlerini temizleme
      - fazla boşlukları azaltma
    """
    if not text:
        return ""
    try:
        text = fix_unicode_escapes(text)
    except Exception:
        pass
    try:
        if 'Ã' in text or 'Ä' in text or 'Å' in text:
            tb = text.encode('latin-1', errors='replace')
            maybe = tb.decode('utf-8', errors='replace')
            if re.search('[çğıöşüÇĞİÖŞÜ]', maybe):
                text = maybe
    except Exception:
        pass
    try:
        text = unicodedata.normalize('NFKC', text)
    except Exception:
        pass
    text = _clean_control_chars(text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def decode_and_fix_text(content: bytes) -> str:
    """
    Byte içeriğini güvenli biçimde decode et ve normalize et.
    """
    if content is None:
        return ""
    encodings = ['utf-8-sig', 'utf-8', 'iso-8859-9', 'cp1254', 'windows-1254', 'latin-1']
    for enc in encodings:
        try:
            decoded = content.decode(enc)
            fixed = normalize_turkish_text(decoded)
            if fixed:
                return fixed
        except Exception:
            continue
    try:
        maybe = content.decode('latin-1', errors='replace').encode('latin-1').decode('utf-8', errors='replace')
        return normalize_turkish_text(maybe)
    except Exception:
        pass
    try:
        return normalize_turkish_text(content.decode('utf-8', errors='replace'))
    except Exception:
        return ""

def cache_set(key: str, value: dict):
    v = dict(value)
    v['timestamp'] = time.time()
    result_cache[key] = v

# ========== TEXT FORMATTING FUNCTIONS ==========
def format_record_as_text(record: dict, record_type: str = "") -> str:
    """
    Tek bir kaydı metin formatında formatlar.
    """
    lines = []
    
    # Başlık ekle
    if record_type:
        lines.append(f"📋 {record_type.upper()} SORGUSU")
        lines.append("="*60)
    
    # Ad Soyad
    if record.get('Ad') or record.get('Soyad'):
        lines.append(f"👤 Ad Soyad: {record.get('Ad', '')} {record.get('Soyad', '')}".strip())
    
    # TC
    if record.get('TC'):
        lines.append(f"🪪 TC: {record['TC']}")
    
    # GSM
    if record.get('Telefon'):
        lines.append(f"📱 Telefon: {record['Telefon']}")
    
    # Plaka
    if record.get('Plaka'):
        lines.append(f"🚗 Plaka: {record['Plaka']}")
    
    # Doğum Bilgileri
    if record.get('DogumYeri') or record.get('DogumTarihi'):
        birth_parts = []
        if record.get('DogumYeri'):
            birth_parts.append(record['DogumYeri'])
        if record.get('DogumTarihi'):
            birth_parts.append(record['DogumTarihi'])
        lines.append(f"🎂 Doğum: {' / '.join(birth_parts)}")
    
    # Aile Bilgileri
    if record.get('AnneAdi'):
        lines.append(f"👩 Anne: {record['AnneAdi']}")
    if record.get('BabaAdi'):
        lines.append(f"👨 Baba: {record['BabaAdi']}")
    
    # Adres Bilgileri
    if record.get('Il') or record.get('Ilce'):
        loc_parts = []
        if record.get('Il'):
            loc_parts.append(record['Il'])
        if record.get('Ilce'):
            loc_parts.append(record['Ilce'])
        lines.append(f"📍 Yer: {' / '.join(loc_parts)}")
    
    # Araç Bilgileri
    if record.get('MarkaModel'):
        lines.append(f"🚙 Marka/Model: {record['MarkaModel']}")
    if record.get('RuhsatNo'):
        lines.append(f"📄 Ruhsat No: {record['RuhsatNo']}")
    if record.get('MotorNo'):
        lines.append(f"⚙️ Motor No: {record['MotorNo']}")
    if record.get('SaseNo'):
        lines.append(f"🔧 Şase No: {record['SaseNo']}")
    
    # İşyeri Bilgileri
    if record.get('IsyeriUnvani'):
        lines.append(f"🏢 İşyeri: {record['IsyeriUnvani']}")
    if record.get('VergiNo'):
        lines.append(f"💰 Vergi No: {record['VergiNo']}")
    
    # Aile Sorgu Bilgileri
    if record.get('AileSira'):
        lines.append(f"👨‍👩‍👧‍👦 Aile Sıra: {record['AileSira']}")
    if record.get('BireySira'):
        lines.append(f"👤 Birey Sıra: {record['BireySira']}")
    if record.get('Yakinlik'):
        lines.append(f"🤝 Yakınlık: {record['Yakinlik']}")
    
    # Diğer Bilgiler
    if record.get('Operator'):
        lines.append(f"📡 Operatör: {record['Operator']}")
    if record.get('KayitTarihi'):
        lines.append(f"📅 Kayıt Tarihi: {record['KayitTarihi']}")
    if record.get('Durum'):
        lines.append(f"📊 Durum: {record['Durum']}")
    if record.get('MedeniDurum'):
        lines.append(f"💍 Medeni Durum: {record['MedeniDurum']}")
    if record.get('Cinsiyet'):
        lines.append(f"⚧ Cinsiyet: {record['Cinsiyet']}")
    
    return "\n".join(lines)

def format_records_as_text(records: list, query: str = "", record_type: str = "") -> str:
    """
    Birden fazla kaydı metin formatında formatlar.
    """
    lines = []
    
    # Başlık
    lines.append("="*60)
    if record_type:
        lines.append(f"📋 {record_type.upper()} SORGUSU - {query}")
    else:
        lines.append(f"📋 SORGUSU - {query}")
    lines.append("="*60 + "\n")
    
    if not records:
        lines.append("❌ Kayıt bulunamadı.\n")
        lines.append("="*60)
        return "\n".join(lines)
    
    lines.append(f"✅ Toplam {len(records)} kayıt bulundu:\n")
    
    for i, rec in enumerate(records, 1):
        lines.append(f"🔸 KAYIT {i}")
        lines.append("-"*40)
        lines.append(format_record_as_text(rec))
        lines.append("")
    
    lines.append("="*60)
    lines.append(f"✅ {len(records)} kayıt listelendi")
    lines.append("="*60)
    
    return "\n".join(lines)

# ========== PARSERS ==========
def _normalize_record_strings(record: dict):
    for k, v in list(record.items()):
        if isinstance(v, str):
            record[k] = normalize_turkish_text(v)
    return record

def extract_simple_records(text: str):
    if not text:
        return []
    text = normalize_turkish_text(text)
    chunks = re.split(r'🧾 TC Sorgu Sonucu|📄 TC Sorgu Sonucu|🔍 TC Sorgu Sonucu', text)
    records = []
    for chunk in chunks:
        tc_match = re.search(r'TC\s*[:=]\s*(\d{11})', chunk)
        if not tc_match:
            continue
        record = {
            'TC': tc_match.group(1),
            'Ad': '',
            'Soyad': '',
            'DogumYeri': '',
            'DogumTarihi': '',
            'AnneAdi': '',
            'BabaAdi': '',
            'Il': '',
            'Ilce': '',
            'Telefon': '',
            'MedeniDurum': '',
            'Cinsiyet': ''
        }
        name_match = re.search(r'Adı Soyadı\s*[:=]\s*([^\n\r]+)|Ad Soyad\s*[:=]\s*([^\n\r]+)', chunk)
        if name_match:
            raw_full_name = (name_match.group(1) or name_match.group(2) or '').strip()
            full_name = normalize_turkish_text(raw_full_name).upper()
            parts = full_name.split()
            if parts:
                record['Ad'] = normalize_turkish_text(parts[0])
                record['Soyad'] = normalize_turkish_text(" ".join(parts[1:]) if len(parts) > 1 else "")
        birth_match = re.search(r'Doğum\s*\(Yer/Tarih\)\s*[:=]\s*([^/]+)\s*/\s*([\d.\-/]+)', chunk)
        if not birth_match:
            birth_match = re.search(r'Doğum\s*[:=]\s*([^/]+)\s*/\s*([\d.\-/]+)', chunk)
        if birth_match:
            record['DogumYeri'] = normalize_turkish_text(birth_match.group(1).strip().title())
            record['DogumTarihi'] = normalize_turkish_text(birth_match.group(2).strip())
        anne_match = re.search(r'Anne\s*\(Ad/TC\)\s*[:=]\s*([^/\n\r]+)', chunk)
        if anne_match:
            record['AnneAdi'] = normalize_turkish_text(anne_match.group(1).strip())
        else:
            anne_match = re.search(r'Anne\s*[:=]\s*([^\n\r]+)', chunk)
            if anne_match:
                record['AnneAdi'] = normalize_turkish_text(anne_match.group(1).strip())
        baba_match = re.search(r'Baba\s*\(Ad/TC\)\s*[:=]\s*([^/\n\r]+)', chunk)
        if baba_match:
            record['BabaAdi'] = normalize_turkish_text(baba_match.group(1).strip())
        else:
            baba_match = re.search(r'Baba\s*[:=]\s*([^\n\r]+)', chunk)
            if baba_match:
                record['BabaAdi'] = normalize_turkish_text(baba_match.group(1).strip())
        loc_match = re.search(r'İl/İlçe/Köy\s*[:=]\s*([^/]+)\s*/\s*([^/\n\r]+)', chunk)
        if loc_match:
            record['Il'] = normalize_turkish_text(loc_match.group(1).strip().title())
            record['Ilce'] = normalize_turkish_text(loc_match.group(2).strip().title())
        else:
            il_match = re.search(r'İl\s*[:=]\s*([^\n\r]+)', chunk)
            if il_match:
                record['Il'] = normalize_turkish_text(il_match.group(1).strip().title())
        phone_match = re.search(r'GSM\s*[:=]\s*([^\n\r]+)', chunk)
        if not phone_match:
            phone_match = re.search(r'Telefon\s*[:=]\s*([^\n\r]+)', chunk)
        if phone_match:
            phone = re.sub(r'\D', '', phone_match.group(1))
            if phone and len(phone) >= 10:
                record['Telefon'] = phone[-10:]
        medeni_match = re.search(r'Medeni/Cinsiyet\s*[:=]\s*([^/]+)\s*/\s*([^\n\r]+)', chunk)
        if medeni_match:
            record['MedeniDurum'] = normalize_turkish_text(medeni_match.group(1).strip())
            record['Cinsiyet'] = normalize_turkish_text(medeni_match.group(2).strip())
        # ensure all string fields are normalized before appending
        _normalize_record_strings(record)
        records.append(record)
    return records

def parse_general_response(text: str):
    if not text:
        return []
    text = normalize_turkish_text(text)
    records = []
    chunks = re.split(r'🧾 TC Sorgu Sonucu|📄 TC Sorgu Sonucu|📱 GSM Sorgu Sonucu|👨‍👩‍👧‍👦 Aile Sorgu Sonucu|🚗 Plaka Sorgu Sonucu|={3,}|\-{3,}', text)
    for chunk in chunks:
        tc_match = re.search(r'TC\s*[:=]\s*(\d{11})', chunk)
        if not tc_match:
            gsm_match = re.search(r'GSM\s*[:=]\s*(\d{10,11})', chunk)
            plaka_match = re.search(r'Plaka\s*[:=]\s*([A-Z0-9]+)', chunk, re.IGNORECASE)
            if not gsm_match and not plaka_match:
                continue
        record = {
            'TC': '', 'Ad': '', 'Soyad': '', 'DogumYeri': '', 'DogumTarihi': '', 'AnneAdi': '', 'BabaAdi': '',
            'Il': '', 'Ilce': '', 'Telefon': '', 'Plaka': '', 'MarkaModel': '', 'RuhsatNo': '', 'MotorNo': '',
            'SaseNo': '', 'IsyeriUnvani': '', 'VergiNo': '', 'AileSira': '', 'BireySira': '', 'Yakinlik': '',
            'Operator': '', 'KayitTarihi': '', 'Durum': '', 'MedeniDurum': '', 'Cinsiyet': ''
        }
        if tc_match:
            record['TC'] = tc_match.group(1)
        gsm_match = re.search(r'GSM\s*[:=]\s*(\d{10})', chunk)
        if gsm_match:
            phone = gsm_match.group(1)
            if len(phone) == 10:
                record['Telefon'] = phone
            elif len(phone) == 11 and phone.startswith('0'):
                record['Telefon'] = phone[1:]
        plaka_match = re.search(r'Plaka\s*[:=]\s*([A-Z0-9]+)', chunk, re.IGNORECASE)
        if plaka_match:
            record['Plaka'] = plaka_match.group(1).upper()
        name_match = re.search(r'Adı Soyadı\s*[:=]\s*([^\n\r]+)|Ad Soyad\s*[:=]\s*([^\n\r]+)', chunk)
        if name_match:
            raw_full_name = (name_match.group(1) or name_match.group(2) or '').strip()
            full_name = normalize_turkish_text(raw_full_name).upper()
            parts = full_name.split()
            if parts:
                record['Ad'] = normalize_turkish_text(parts[0])
                record['Soyad'] = normalize_turkish_text(" ".join(parts[1:]) if len(parts) > 1 else "")
        birth_match = re.search(r'Doğum\s*\(Yer/Tarih\)\s*[:=]\s*([^/]+)\s*/\s*([\d.\-/]+)', chunk)
        if not birth_match:
            birth_match = re.search(r'Doğum\s*[:=]\s*([^/]+)\s*/\s*([\d.\-/]+)', chunk)
        if birth_match:
            record['DogumYeri'] = normalize_turkish_text(birth_match.group(1).strip().title())
            record['DogumTarihi'] = normalize_turkish_text(birth_match.group(2).strip())
        anne_match = re.search(r'Anne\s*\(Ad/TC\)\s*[:=]\s*([^/\n\r]+)', chunk)
        if anne_match:
            record['AnneAdi'] = normalize_turkish_text(anne_match.group(1).strip())
        else:
            anne_match = re.search(r'Anne\s*[:=]\s*([^\n\r]+)', chunk)
            if anne_match:
                record['AnneAdi'] = normalize_turkish_text(anne_match.group(1).strip())
        baba_match = re.search(r'Baba\s*\(Ad/TC\)\s*[:=]\s*([^/\n\r]+)', chunk)
        if baba_match:
            record['BabaAdi'] = normalize_turkish_text(baba_match.group(1).strip())
        else:
            baba_match = re.search(r'Baba\s*[:=]\s*([^\n\r]+)', chunk)
            if baba_match:
                record['BabaAdi'] = normalize_turkish_text(baba_match.group(1).strip())
        loc_match = re.search(r'İl/İlçe/Köy\s*[:=]\s*([^/]+)\s*/\s*([^/\n\r]+)', chunk)
        if loc_match:
            record['Il'] = normalize_turkish_text(loc_match.group(1).strip().title())
            record['Ilce'] = normalize_turkish_text(loc_match.group(2).strip().title())
        else:
            il_match = re.search(r'İl\s*[:=]\s*([^\n\r]+)', chunk)
            if il_match:
                record['Il'] = normalize_turkish_text(il_match.group(1).strip().title())
            ilce_match = re.search(r'İlçe\s*[:=]\s*([^\n\r]+)', chunk)
            if ilce_match:
                record['Ilce'] = normalize_turkish_text(ilce_match.group(1).strip().title())
        model_match = re.search(r'Marka/Model\s*[:=]\s*([^\n\r]+)', chunk)
        if model_match:
            record['MarkaModel'] = normalize_turkish_text(model_match.group(1).strip())
        ruhsat_match = re.search(r'Ruhsat No\s*[:=]\s*([^\n\r]+)', chunk)
        if ruhsat_match:
            record['RuhsatNo'] = normalize_turkish_text(ruhsat_match.group(1).strip())
        motor_match = re.search(r'Motor No\s*[:=]\s*([^\n\r]+)', chunk)
        if motor_match:
            record['MotorNo'] = normalize_turkish_text(motor_match.group(1).strip())
        sase_match = re.search(r'Şase No\s*[:=]\s*([^\n\r]+)', chunk)
        if sase_match:
            record['SaseNo'] = normalize_turkish_text(sase_match.group(1).strip())
        unvan_match = re.search(r'Ünvan\s*[:=]\s*([^\n\r]+)|İşyeri Ünvanı\s*[:=]\s*([^\n\r]+)', chunk)
        if unvan_match:
            record['IsyeriUnvani'] = normalize_turkish_text((unvan_match.group(1) or unvan_match.group(2) or '').strip())
        vergi_match = re.search(r'Vergi No\s*[:=]\s*([^\n\r]+)', chunk)
        if vergi_match:
            record['VergiNo'] = normalize_turkish_text(vergi_match.group(1).strip())
        aile_match = re.search(r'Aile/Birey Sıra\s*[:=]\s*([^/]+)\s*/\s*([^\n\r]+)', chunk)
        if aile_match:
            record['AileSira'] = normalize_turkish_text(aile_match.group(1).strip())
            record['BireySira'] = normalize_turkish_text(aile_match.group(2).strip())
        yakinlik_match = re.search(r'Yakınlık\s*[:=]\s*([^\n\r]+)', chunk)
        if yakinlik_match:
            record['Yakinlik'] = normalize_turkish_text(yakinlik_match.group(1).strip())
        operator_match = re.search(r'Operatör\s*[:=]\s*([^\n\r]+)', chunk)
        if operator_match:
            record['Operator'] = normalize_turkish_text(operator_match.group(1).strip())
        tarih_match = re.search(r'Kayıt Tarihi\s*[:=]\s*([^\n\r]+)', chunk)
        if tarih_match:
            record['KayitTarihi'] = normalize_turkish_text(tarih_match.group(1).strip())
        durum_match = re.search(r'Durum\s*[:=]\s*([^\n\r]+)', chunk)
        if durum_match:
            record['Durum'] = normalize_turkish_text(durum_match.group(1).strip())
        medeni_match = re.search(r'Medeni/Cinsiyet\s*[:=]\s*([^/]+)\s*/\s*([^\n\r]+)', chunk)
        if medeni_match:
            record['MedeniDurum'] = normalize_turkish_text(medeni_match.group(1).strip())
            record['Cinsiyet'] = normalize_turkish_text(medeni_match.group(2).strip())
        # normalize all string fields before appending
        _normalize_record_strings(record)
        records.append(record)
    return records

# ========== TELEGRAM CLIENT ==========
async def get_client():
    global client, loop
    if client is None:
        if loop is None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH, loop=loop, connection_retries=3, retry_delay=3, timeout=60)
    return client

async def query_bot_with_command(command: str, timeout: int = 120):
    try:
        client = await get_client()
        if not client.is_connected():
            await client.connect()
        async with client.conversation(BOT_USERNAME, timeout=timeout + 30) as conv:
            await conv.send_message(command)
            start_ts = time.time()
            raw_text = ""
            got_file = False
            while time.time() - start_ts < timeout:
                try:
                    response = await conv.get_response(timeout=20)
                except asyncio.TimeoutError:
                    continue
                text = getattr(response, 'text', '') or ''
                if text and any(word in text.lower() for word in ['sorgu yapılıyor', 'işlem devam', 'lütfen bekleyin', 'birazdan', 'yakında']):
                    continue
                # butonlarla .txt indirme
                if hasattr(response, 'buttons') and response.buttons:
                    for row in response.buttons:
                        for btn in row:
                            btn_text = str(getattr(btn, 'text', '')).lower()
                            if any(k in btn_text for k in ['txt', 'dosya', '.txt', 'indir', 'download', 'gör', 'aç']):
                                try:
                                    await btn.click()
                                    try:
                                        file_msg = await conv.get_response(timeout=30)
                                    except asyncio.TimeoutError:
                                        continue
                                    if file_msg and hasattr(file_msg, 'media') and file_msg.media:
                                        file_path = await client.download_media(file_msg)
                                        if file_path and os.path.exists(file_path):
                                            with open(file_path, 'rb') as f:
                                                content = f.read()
                                            raw_text = decode_and_fix_text(content)
                                            got_file = True
                                            try:
                                                os.remove(file_path)
                                            except Exception:
                                                pass
                                            if got_file:
                                                return raw_text
                                except Exception:
                                    continue
                # medya mesajı
                if hasattr(response, 'media') and response.media:
                    try:
                        file_path = await client.download_media(response)
                        if file_path and os.path.exists(file_path):
                            with open(file_path, 'rb') as f:
                                content = f.read()
                            raw_text = decode_and_fix_text(content)
                            try:
                                os.remove(file_path)
                            except Exception:
                                pass
                            return raw_text
                    except Exception:
                        pass
                if text:
                    # normalize incoming text early, so escape sequences are fixed
                    text = normalize_turkish_text(text)
                    if re.search(r'\d{11}', text) or re.search(r'GSM\s*[:=]\s*\d', text) or re.search(r'Plaka\s*[:=]', text, re.IGNORECASE):
                        raw_text = text
                        return raw_text
                    if text.strip() and not any(word in text.lower() for word in ['sorgu yapılıyor', 'işlem devam']):
                        raw_text = text
                        return raw_text
                await asyncio.sleep(1)
            if raw_text:
                return raw_text
            else:
                return "❌ Sorgu zaman aşımına uğradı veya yanıt alınamadı"
    except Exception as e:
        return f"Error: {str(e)}"

def sync_query_bot(command: str) -> str:
    global loop
    try:
        if loop is None or loop.is_closed():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        return loop.run_until_complete(query_bot_with_command(command))
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(query_bot_with_command(command))
    except Exception as e:
        return f"Error: {str(e)}"

# ========== PARAM CLEANERS ==========
def clean_tc(tc):
    tc = re.sub(r'\D', '', tc)
    if len(tc) == 11:
        return tc
    return None

def clean_gsm(gsm):
    gsm = re.sub(r'\D', '', gsm)
    if gsm.startswith('0'):
        gsm = gsm[1:]
    if len(gsm) == 10:
        return gsm
    elif len(gsm) > 10:
        return gsm[-10:]
    return None

def clean_plaka(plaka):
    plaka = re.sub(r'[^A-Z0-9]', '', plaka.upper())
    if len(plaka) >= 4:
        return plaka
    return None

def make_result(success: bool, query: str, records: list, raw_text: str):
    clean_preview = normalize_turkish_text(raw_text or '')[:500]
    # normalize every string field inside records
    for rec in records:
        _normalize_record_strings(rec)
    if success:
        res = {'success': True, 'query': query, 'count': len(records), 'records': records, 'raw_preview': clean_preview}
    else:
        res = {'success': False, 'query': query, 'error': 'Kayıt bulunamadı', 'raw_preview': clean_preview}
    return res

# ========== COMMON TEXT RESPONSE FUNCTION ==========
def make_text_response(records: list, query: str, record_type: str = ""):
    """
    Tüm text endpoint'leri için ortak fonksiyon.
    """
    if not records:
        return Response(f'❌ {query} için kayıt bulunamadı.', content_type='text/plain; charset=utf-8')
    
    formatted_text = format_records_as_text(records, query, record_type)
    return Response(formatted_text, content_type='text/plain; charset=utf-8')

# ========== ENDPOINTS ==========

# JSON Endpoints
@app.route('/tc', methods=['GET'])
def api_tc():
    tc = request.args.get('tc', '').strip()
    tc = clean_tc(tc)
    if not tc:
        return jsonify({'success': False, 'error': 'Geçerli bir 11 haneli TC kimlik numarası giriniz'}), 400
    cache_key = f"tc_{tc}"
    if cache_key in result_cache:
        return jsonify(result_cache[cache_key])
    command = f"/tc {tc}"
    raw_text = sync_query_bot(command)
    records = parse_general_response(raw_text)
    res = make_result(bool(records), f"TC: {tc}", records, raw_text)
    cache_set(cache_key, res)
    return jsonify(res)

@app.route('/tc2', methods=['GET'])
def api_tc2():
    tc = request.args.get('tc', '').strip()
    tc = clean_tc(tc)
    if not tc:
        return jsonify({'success': False, 'error': 'Geçerli bir 11 haneli TC kimlik numarası giriniz'}), 400
    cache_key = f"tc2_{tc}"
    if cache_key in result_cache:
        return jsonify(result_cache[cache_key])
    command = f"/tc2 {tc}"
    raw_text = sync_query_bot(command)
    records = parse_general_response(raw_text)
    res = make_result(bool(records), f"TC2: {tc}", records, raw_text)
    cache_set(cache_key, res)
    return jsonify(res)

@app.route('/gsm', methods=['GET'])
def api_gsm():
    gsm = request.args.get('gsm', '').strip()
    gsm = clean_gsm(gsm)
    if not gsm:
        return jsonify({'success': False, 'error': 'Geçerli bir telefon numarası giriniz'}), 400
    cache_key = f"gsm_{gsm}"
    if cache_key in result_cache:
        return jsonify(result_cache[cache_key])
    command = f"/gsm {gsm}"
    raw_text = sync_query_bot(command)
    records = parse_general_response(raw_text)
    res = make_result(bool(records), f"GSM: {gsm}", records, raw_text)
    cache_set(cache_key, res)
    return jsonify(res)

@app.route('/gsm2', methods=['GET'])
def api_gsm2():
    gsm = request.args.get('gsm', '').strip()
    gsm = clean_gsm(gsm)
    if not gsm:
        return jsonify({'success': False, 'error': 'Geçerli bir telefon numarası giriniz'}), 400
    cache_key = f"gsm2_{gsm}"
    if cache_key in result_cache:
        return jsonify(result_cache[cache_key])
    command = f"/gsm2 {gsm}"
    raw_text = sync_query_bot(command)
    records = parse_general_response(raw_text)
    res = make_result(bool(records), f"GSM2: {gsm}", records, raw_text)
    cache_set(cache_key, res)
    return jsonify(res)

@app.route('/aile', methods=['GET'])
def api_aile():
    tc = request.args.get('tc', '').strip()
    tc = clean_tc(tc)
    if not tc:
        return jsonify({'success': False, 'error': 'Geçerli bir 11 haneli TC kimlik numarası giriniz'}), 400
    cache_key = f"aile_{tc}"
    if cache_key in result_cache:
        return jsonify(result_cache[cache_key])
    command = f"/aile {tc}"
    raw_text = sync_query_bot(command)
    records = parse_general_response(raw_text)
    res = make_result(bool(records), f"Aile: {tc}", records, raw_text)
    cache_set(cache_key, res)
    return jsonify(res)

@app.route('/sulale', methods=['GET'])
def api_sulale():
    tc = request.args.get('tc', '').strip()
    tc = clean_tc(tc)
    if not tc:
        return jsonify({'success': False, 'error': 'Geçerli bir 11 haneli TC kimlik numarası giriniz'}), 400
    cache_key = f"sulale_{tc}"
    if cache_key in result_cache:
        return jsonify(result_cache[cache_key])
    command = f"/sulale {tc}"
    raw_text = sync_query_bot(command)
    records = parse_general_response(raw_text)
    res = make_result(bool(records), f"Sülale: {tc}", records, raw_text)
    cache_set(cache_key, res)
    return jsonify(res)

@app.route('/hane', methods=['GET'])
def api_hane():
    tc = request.args.get('tc', '').strip()
    tc = clean_tc(tc)
    if not tc:
        return jsonify({'success': False, 'error': 'Geçerli bir 11 haneli TC kimlik numarası giriniz'}), 400
    cache_key = f"hane_{tc}"
    if cache_key in result_cache:
        return jsonify(result_cache[cache_key])
    command = f"/hane {tc}"
    raw_text = sync_query_bot(command)
    records = parse_general_response(raw_text)
    res = make_result(bool(records), f"Hane: {tc}", records, raw_text)
    cache_set(cache_key, res)
    return jsonify(res)

@app.route('/isyeri', methods=['GET'])
def api_isyeri():
    tc = request.args.get('tc', '').strip()
    tc = clean_tc(tc)
    if not tc:
        return jsonify({'success': False, 'error': 'Geçerli bir 11 haneli TC kimlik numarası giriniz'}), 400
    cache_key = f"isyeri_{tc}"
    if cache_key in result_cache:
        return jsonify(result_cache[cache_key])
    command = f"/isyeri {tc}"
    raw_text = sync_query_bot(command)
    records = parse_general_response(raw_text)
    res = make_result(bool(records), f"İşyeri: {tc}", records, raw_text)
    cache_set(cache_key, res)
    return jsonify(res)

@app.route('/plaka', methods=['GET'])
def api_plaka():
    plaka = request.args.get('plaka', '').strip()
    plaka = clean_plaka(plaka)
    if not plaka:
        return jsonify({'success': False, 'error': 'Geçerli bir plaka numarası giriniz'}), 400
    cache_key = f"plaka_{plaka}"
    if cache_key in result_cache:
        return jsonify(result_cache[cache_key])
    command = f"/plaka {plaka}"
    raw_text = sync_query_bot(command)
    records = parse_general_response(raw_text)
    res = make_result(bool(records), f"Plaka: {plaka}", records, raw_text)
    cache_set(cache_key, res)
    return jsonify(res)

@app.route('/vesika', methods=['GET'])
def api_vesika():
    tc = request.args.get('tc', '').strip()
    tc = clean_tc(tc)
    if not tc:
        return jsonify({'success': False, 'error': 'Geçerli bir 11 haneli TC kimlik numarası giriniz'}), 400
    cache_key = f"vesika_{tc}"
    if cache_key in result_cache:
        return jsonify(result_cache[cache_key])
    command = f"/vesika {tc}"
    raw_text = sync_query_bot(command)
    records = parse_general_response(raw_text)
    res = make_result(bool(records), f"Vesika: {tc}", records, raw_text)
    cache_set(cache_key, res)
    return jsonify(res)

@app.route('/ad', methods=['GET'])
def api_ad():
    name = request.args.get('name', '').strip()
    surname = request.args.get('surname', '').strip()
    il = request.args.get('il', '').strip().title()
    adres = request.args.get('adres', '').strip()
    if not name or not surname:
        return jsonify({'success': False, 'error': 'name ve surname gerekli'}), 400
    # normalize input early
    name = normalize_turkish_text(name).upper()
    surname = normalize_turkish_text(surname).upper()
    command = f"/ad {name} {surname}"
    if il:
        command += f" -il {il}"
    if adres:
        command += f" -adres {adres}"
    cache_key = f"ad_{name}_{surname}_{il}_{adres}".replace(' ', '_')
    if cache_key in result_cache:
        return jsonify(result_cache[cache_key])
    raw_text = sync_query_bot(command)
    records = extract_simple_records(raw_text)
    res = make_result(bool(records), f"Ad: {name} {surname}", records, raw_text)
    cache_set(cache_key, res)
    return jsonify(res)

@app.route('/query', methods=['GET'])
def api_query():
    name = request.args.get('name', '') or request.args.get('first_name', '')
    surname = request.args.get('surname', '') or request.args.get('last_name', '')
    name = name.strip()
    surname = surname.strip()
    if not name or not surname:
        return jsonify({'success': False, 'error': 'name ve surname gerekli'}), 400
    name = normalize_turkish_text(name).upper()
    surname = normalize_turkish_text(surname).upper()
    cache_key = f"query_{name}_{surname}"
    if cache_key in result_cache:
        return jsonify(result_cache[cache_key])
    command = f"/ad {name} {surname}"
    raw_text = sync_query_bot(command)
    records = extract_simple_records(raw_text)
    res = make_result(bool(records), f"{name} {surname}", records, raw_text)
    cache_set(cache_key, res)
    return jsonify(res)

# TEXT Endpoints
@app.route('/text', methods=['GET'])
def api_text():
    name = request.args.get('name', '') or request.args.get('first_name', '')
    surname = request.args.get('surname', '') or request.args.get('last_name', '')
    name = name.strip()
    surname = surname.strip()
    if not name or not surname:
        return Response('❌ Hata: name ve surname gerekli', content_type='text/plain; charset=utf-8')
    name = normalize_turkish_text(name).upper()
    surname = normalize_turkish_text(surname).upper()
    command = f"/ad {name} {surname}"
    raw_text = sync_query_bot(command)
    records = extract_simple_records(raw_text)
    return make_text_response(records, f"{name} {surname}", "AD SOYAD")

@app.route('/tc/text', methods=['GET'])
def api_tc_text():
    tc = request.args.get('tc', '').strip()
    tc = clean_tc(tc)
    if not tc:
        return Response('❌ Hata: Geçerli bir 11 haneli TC kimlik numarası giriniz', content_type='text/plain; charset=utf-8')
    command = f"/tc {tc}"
    raw_text = sync_query_bot(command)
    records = parse_general_response(raw_text)
    return make_text_response(records, f"TC: {tc}", "TC")

@app.route('/tc2/text', methods=['GET'])
def api_tc2_text():
    tc = request.args.get('tc', '').strip()
    tc = clean_tc(tc)
    if not tc:
        return Response('❌ Hata: Geçerli bir 11 haneli TC kimlik numarası giriniz', content_type='text/plain; charset=utf-8')
    command = f"/tc2 {tc}"
    raw_text = sync_query_bot(command)
    records = parse_general_response(raw_text)
    return make_text_response(records, f"TC2: {tc}", "TC2")

@app.route('/gsm/text', methods=['GET'])
def api_gsm_text():
    gsm = request.args.get('gsm', '').strip()
    gsm = clean_gsm(gsm)
    if not gsm:
        return Response('❌ Hata: Geçerli bir telefon numarası giriniz', content_type='text/plain; charset=utf-8')
    command = f"/gsm {gsm}"
    raw_text = sync_query_bot(command)
    records = parse_general_response(raw_text)
    return make_text_response(records, f"GSM: {gsm}", "GSM")

@app.route('/gsm2/text', methods=['GET'])
def api_gsm2_text():
    gsm = request.args.get('gsm', '').strip()
    gsm = clean_gsm(gsm)
    if not gsm:
        return Response('❌ Hata: Geçerli bir telefon numarası giriniz', content_type='text/plain; charset=utf-8')
    command = f"/gsm2 {gsm}"
    raw_text = sync_query_bot(command)
    records = parse_general_response(raw_text)
    return make_text_response(records, f"GSM2: {gsm}", "GSM2")

@app.route('/aile/text', methods=['GET'])
def api_aile_text():
    tc = request.args.get('tc', '').strip()
    tc = clean_tc(tc)
    if not tc:
        return Response('❌ Hata: Geçerli bir 11 haneli TC kimlik numarası giriniz', content_type='text/plain; charset=utf-8')
    command = f"/aile {tc}"
    raw_text = sync_query_bot(command)
    records = parse_general_response(raw_text)
    return make_text_response(records, f"Aile: {tc}", "AİLE")

@app.route('/sulale/text', methods=['GET'])
def api_sulale_text():
    tc = request.args.get('tc', '').strip()
    tc = clean_tc(tc)
    if not tc:
        return Response('❌ Hata: Geçerli bir 11 haneli TC kimlik numarası giriniz', content_type='text/plain; charset=utf-8')
    command = f"/sulale {tc}"
    raw_text = sync_query_bot(command)
    records = parse_general_response(raw_text)
    return make_text_response(records, f"Sülale: {tc}", "SÜLALE")

@app.route('/hane/text', methods=['GET'])
def api_hane_text():
    tc = request.args.get('tc', '').strip()
    tc = clean_tc(tc)
    if not tc:
        return Response('❌ Hata: Geçerli bir 11 haneli TC kimlik numarası giriniz', content_type='text/plain; charset=utf-8')
    command = f"/hane {tc}"
    raw_text = sync_query_bot(command)
    records = parse_general_response(raw_text)
    return make_text_response(records, f"Hane: {tc}", "HANE")

@app.route('/isyeri/text', methods=['GET'])
def api_isyeri_text():
    tc = request.args.get('tc', '').strip()
    tc = clean_tc(tc)
    if not tc:
        return Response('❌ Hata: Geçerli bir 11 haneli TC kimlik numarası giriniz', content_type='text/plain; charset=utf-8')
    command = f"/isyeri {tc}"
    raw_text = sync_query_bot(command)
    records = parse_general_response(raw_text)
    return make_text_response(records, f"İşyeri: {tc}", "İŞYERİ")

@app.route('/plaka/text', methods=['GET'])
def api_plaka_text():
    plaka = request.args.get('plaka', '').strip()
    plaka = clean_plaka(plaka)
    if not plaka:
        return Response('❌ Hata: Geçerli bir plaka numarası giriniz', content_type='text/plain; charset=utf-8')
    command = f"/plaka {plaka}"
    raw_text = sync_query_bot(command)
    records = parse_general_response(raw_text)
    return make_text_response(records, f"Plaka: {plaka}", "PLAKA")

@app.route('/vesika/text', methods=['GET'])
def api_vesika_text():
    tc = request.args.get('tc', '').strip()
    tc = clean_tc(tc)
    if not tc:
        return Response('❌ Hata: Geçerli bir 11 haneli TC kimlik numarası giriniz', content_type='text/plain; charset=utf-8')
    command = f"/vesika {tc}"
    raw_text = sync_query_bot(command)
    records = parse_general_response(raw_text)
    return make_text_response(records, f"Vesika: {tc}", "VESİKA")

@app.route('/raw', methods=['GET'])
def api_raw():
    name = request.args.get('name', '') or request.args.get('first_name', '')
    surname = request.args.get('surname', '') or request.args.get('last_name', '')
    name = name.strip()
    surname = surname.strip()
    if not name or not surname:
        return Response('❌ Hata: name ve surname gerekli', content_type='text/plain; charset=utf-8')
    name = normalize_turkish_text(name).upper()
    surname = normalize_turkish_text(surname).upper()
    command = f"/ad {name} {surname}"
    raw_text = sync_query_bot(command)
    # return normalized raw text so unicode escapes are resolved
    normalized_raw = normalize_turkish_text(raw_text or '')
    output = f"🔍 RAW TEXT FOR: {name} {surname}\n"
    output += "="*60 + "\n\n"
    output += (normalized_raw)[:2000] + ("\n\n[...truncated...]" if normalized_raw and len(normalized_raw) > 2000 else "")
    return Response(output, content_type='text/plain; charset=utf-8')

@app.route('/test', methods=['GET'])
def api_test():
    return jsonify({
        'status': '✅ API çalışıyor',
        'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
        'endpoints': [
            {'name': 'TC Sorgu (JSON)', 'url': '/tc?tc=11111111110'},
            {'name': 'TC Sorgu (Text)', 'url': '/tc/text?tc=11111111110'},
            {'name': 'TC2 Sorgu (JSON)', 'url': '/tc2?tc=11111111110'},
            {'name': 'TC2 Sorgu (Text)', 'url': '/tc2/text?tc=11111111110'},
            {'name': 'GSM Sorgu (JSON)', 'url': '/gsm?gsm=5346149118'},
            {'name': 'GSM Sorgu (Text)', 'url': '/gsm/text?gsm=5346149118'},
            {'name': 'GSM2 Sorgu (JSON)', 'url': '/gsm2?gsm=5346149118'},
            {'name': 'GSM2 Sorgu (Text)', 'url': '/gsm2/text?gsm=5346149118'},
            {'name': 'Aile Sorgu (JSON)', 'url': '/aile?tc=11111111110'},
            {'name': 'Aile Sorgu (Text)', 'url': '/aile/text?tc=11111111110'},
            {'name': 'Sülale Sorgu (JSON)', 'url': '/sulale?tc=11111111110'},
            {'name': 'Sülale Sorgu (Text)', 'url': '/sulale/text?tc=11111111110'},
            {'name': 'Hane Sorgu (JSON)', 'url': '/hane?tc=11111111110'},
            {'name': 'Hane Sorgu (Text)', 'url': '/hane/text?tc=11111111110'},
            {'name': 'İşyeri Sorgu (JSON)', 'url': '/isyeri?tc=11111111110'},
            {'name': 'İşyeri Sorgu (Text)', 'url': '/isyeri/text?tc=11111111110'},
            {'name': 'Plaka Sorgu (JSON)', 'url': '/plaka?plaka=34AKP34'},
            {'name': 'Plaka Sorgu (Text)', 'url': '/plaka/text?plaka=34AKP34'},
            {'name': 'Vesika Sorgu (JSON)', 'url': '/vesika?tc=11111111110'},
            {'name': 'Vesika Sorgu (Text)', 'url': '/vesika/text?tc=11111111110'},
            {'name': 'Ad Soyad Sorgu (JSON)', 'url': '/ad?name=EYMEN&surname=YAVUZ'},
            {'name': 'Ad Soyad Sorgu (Text)', 'url': '/text?name=EYMEN&surname=YAVUZ'},
            {'name': 'Query Endpoint', 'url': '/query?name=EYMEN&surname=YAVUZ'},
            {'name': 'Raw Output', 'url': '/raw?name=EYMEN&surname=YAVUZ'}
        ]
    })

@app.route('/status', methods=['GET'])
def api_status():
    return jsonify({
        'status': 'online',
        'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
        'cache_size': len(result_cache),
        'endpoints': [
            '/tc', '/tc/text', '/tc2', '/tc2/text', '/gsm', '/gsm/text', '/gsm2', '/gsm2/text',
            '/aile', '/aile/text', '/sulale', '/sulale/text', '/hane', '/hane/text', 
            '/isyeri', '/isyeri/text', '/plaka', '/plaka/text', '/vesika', '/vesika/text',
            '/ad', '/query', '/text', '/raw', '/test', '/status'
        ]
    })

@app.route('/')
def index():
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>TC Sorgu API</title>
        <style>
            body{font-family:Arial,Helvetica,sans-serif;padding:20px}
            .card{background:#fff;padding:20px;border-radius:8px;box-shadow:0 6px 20px rgba(0,0,0,0.1);max-width:1000px;margin:0 auto}
            code{background:#f1f1f1;padding:3px 6px;border-radius:4px}
        </style>
    </head>
    <body>
        <div class="card">
            <h1>🔍 TC Sorgu API</h1>
            <p>API çalışıyor. örnek endpoint: <code>/query?name=EYMEN&surname=YAVUZ</code></p>
            <p>Tüm sorgular için JSON ve Text format desteği mevcut.</p>
        </div>
    </body>
    </html>
    """
    return html

# ========== CACHE CLEANUP ==========
def cleanup_cache():
    global result_cache
    now = time.time()
    keys = [k for k, v in result_cache.items() if isinstance(v, dict) and 'timestamp' in v and (now - v['timestamp'] > CACHE_TTL)]
    for k in keys:
        result_cache.pop(k, None)

def periodic_cache_cleanup():
    while True:
        time.sleep(60)
        cleanup_cache()

cleanup_thread = threading.Thread(target=periodic_cache_cleanup, daemon=True)
cleanup_thread.start()

# ========== MAIN ==========
if __name__ == '__main__':
    print('🚀 API Başlatılıyor...')
    print('🌐 http://127.0.0.1:5000')
    print('📝 Tüm sorgular için JSON ve Text format desteği eklendi!')
    app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False)
