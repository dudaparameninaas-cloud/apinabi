# -*- coding: utf-8 -*-
import asyncio
import os
import re
import json
import sys
import time
import unicodedata
import threading
from telethon import TelegramClient
from telethon.sessions import StringSession
from flask import Flask, request, jsonify, Response

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False

# Force UTF-8
try:
    sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)
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
client_lock = threading.Lock()
loop = None
result_cache = {}

# ========== IMPROVED UTILITIES ==========

def fix_unicode_escapes(text: str) -> str:
    """Unicode escape karakterlerini (\u0130, \u00e7 vb.) düzgün Türkçe karakterlere çevir"""
    if not text:
        return ""
    
    # Unicode escape'leri decode et
    try:
        # JSON decode gibi davranarak unicode escape'leri çöz
        if '\\u' in text:
            # Önce \\u'leri \u'ye çevir
            text = text.replace('\\\\u', '\\u')
            # JSON formatında decode et
            decoded = bytes(text, 'utf-8').decode('unicode_escape')
            return decoded
    except Exception as e:
        print(f"Unicode escape fix error: {e}")
    
    return text


def normalize_turkish_text(text: str) -> str:
    """Türkçe metni normalize et"""
    if not text:
        return ""
    
    # Önce unicode escape'leri düzelt
    text = fix_unicode_escapes(text)
    
    # Unicode normalize
    try:
        text = unicodedata.normalize('NFKC', text)
    except:
        pass
    
    # Türkçe karakter mapping
    turkish_mapping = {
        # Unicode escape sonrası common fixes
        '\u0130': 'İ',  # LATIN CAPITAL LETTER I WITH DOT ABOVE
        '\u0131': 'ı',  # LATIN SMALL LETTER DOTLESS I
        '\u011f': 'ğ',  # LATIN SMALL LETTER G WITH BREVE
        '\u011e': 'Ğ',  # LATIN CAPITAL LETTER G WITH BREVE
        '\u015f': 'ş',  # LATIN SMALL LETTER S WITH CEDILLA
        '\u015e': 'Ş',  # LATIN CAPITAL LETTER S WITH CEDILLA
        '\u00e7': 'ç',  # LATIN SMALL LETTER C WITH CEDILLA
        '\u00c7': 'Ç',  # LATIN CAPITAL LETTER C WITH CEDILLA
        '\u00fc': 'ü',  # LATIN SMALL LETTER U WITH DIAERESIS
        '\u00dc': 'Ü',  # LATIN CAPITAL LETTER U WITH DIAERESIS
        '\u00f6': 'ö',  # LATIN SMALL LETTER O WITH DIAERESIS
        '\u00d6': 'Ö',  # LATIN CAPITAL LETTER O WITH DIAERESIS
        '\u00e4': 'ä',  # LATIN SMALL LETTER A WITH DIAERESIS
        '\u00c4': 'Ä',  # LATIN CAPITAL LETTER A WITH DIAERESIS
        
        # Diğer yaygın karakterler
        '\u2018': "'",  # Left single quotation mark
        '\u2019': "'",  # Right single quotation mark
        '\u201c': '"',  # Left double quotation mark
        '\u201d': '"',  # Right double quotation mark
        '\u2013': '-',  # En dash
        '\u2014': '-',  # Em dash
        '\u2026': '...', # Horizontal ellipsis
        '\u00a0': ' ',  # Non-breaking space
        '\u200b': '',   # Zero width space
        '\u200e': '',   # Left-to-right mark
        '\u200f': '',   # Right-to-left mark
        '\u202a': '',   # Left-to-right embedding
        '\u202c': '',   # Pop directional formatting
        '\ufeff': '',   # Byte order mark
    }
    
    result = text
    for wrong, correct in turkish_mapping.items():
        result = result.replace(wrong, correct)
    
    # Çoklu boşlukları temizle
    result = re.sub(r'\s+', ' ', result)
    
    return result.strip()


def decode_and_fix_text(content: bytes) -> str:
    """Bytes'ı decode et ve Türkçe karakterleri düzelt"""
    encodings = ['utf-8', 'iso-8859-9', 'cp1254', 'windows-1254', 'latin-1']
    
    for encoding in encodings:
        try:
            decoded = content.decode(encoding)
            # Türkçe karakterleri düzelt
            fixed = normalize_turkish_text(decoded)
            return fixed
        except UnicodeDecodeError:
            continue
    
    # Hiçbiri çalışmazsa, errors='replace' ile decode et
    try:
        decoded = content.decode('utf-8', errors='replace')
        return normalize_turkish_text(decoded)
    except:
        return content.decode('utf-8', errors='ignore')


def extract_simple_records(text: str):
    """Gelişmiş Kayıt Ayıklayıcı - Türkçe karakter düzeltmeli"""
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

        # Ad Soyad Ayıklama
        name_match = re.search(r'Adı Soyadı\s*[:=]\s*([^\n\r]+)|Ad Soyad\s*[:=]\s*([^\n\r]+)', chunk)
        if name_match:
            full_name = (name_match.group(1) or name_match.group(2) or '').strip().upper()
            parts = full_name.split()
            if parts:
                record['Ad'] = normalize_turkish_text(parts[0])
                record['Soyad'] = normalize_turkish_text(" ".join(parts[1:]) if len(parts) > 1 else "")

        # Doğum Yeri ve Tarihi
        birth_match = re.search(r'Doğum\s*\(Yer/Tarih\)\s*[:=]\s*([^/]+)\s*/\s*([\d-]+)', chunk)
        if not birth_match:
            birth_match = re.search(r'Doğum\s*[:=]\s*([^/]+)\s*/\s*([\d-]+)', chunk)
        if birth_match:
            record['DogumYeri'] = normalize_turkish_text(birth_match.group(1).strip().title())
            record['DogumTarihi'] = birth_match.group(2).strip()

        # Anne ve Baba Adı
        anne_match = re.search(r'Anne\s*\(Ad/TC\)\s*[:=]\s*([^/\n\r]+)', chunk)
        if anne_match:
            record['AnneAdi'] = normalize_turkish_text(anne_match.group(1).strip().upper())
        else:
            anne_match = re.search(r'Anne\s*[:=]\s*([^\n\r]+)', chunk)
            if anne_match:
                record['AnneAdi'] = normalize_turkish_text(anne_match.group(1).strip().upper())

        baba_match = re.search(r'Baba\s*\(Ad/TC\)\s*[:=]\s*([^/\n\r]+)', chunk)
        if baba_match:
            record['BabaAdi'] = normalize_turkish_text(baba_match.group(1).strip().upper())
        else:
            baba_match = re.search(r'Baba\s*[:=]\s*([^\n\r]+)', chunk)
            if baba_match:
                record['BabaAdi'] = normalize_turkish_text(baba_match.group(1).strip().upper())

        # İl / İlçe
        loc_match = re.search(r'İl/İlçe/Köy\s*[:=]\s*([^/]+)\s*/\s*([^/\n\r]+)', chunk)
        if loc_match:
            record['Il'] = normalize_turkish_text(loc_match.group(1).strip().title())
            record['Ilce'] = normalize_turkish_text(loc_match.group(2).strip().title())
        else:
            il_match = re.search(r'İl\s*[:=]\s*([^\n\r]+)', chunk)
            if il_match:
                record['Il'] = normalize_turkish_text(il_match.group(1).strip().title())

        # Telefon
        phone_match = re.search(r'GSM\s*[:=]\s*([^\n\r]+)', chunk)
        if not phone_match:
            phone_match = re.search(r'Telefon\s*[:=]\s*([^\n\r]+)', chunk)
        if phone_match:
            phone = re.sub(r'\D', '', phone_match.group(1))
            if phone and len(phone) >= 10:
                record['Telefon'] = phone[-10:]  # Son 10 hane

        # Medeni Durum / Cinsiyet
        medeni_match = re.search(r'Medeni/Cinsiyet\s*[:=]\s*([^/]+)\s*/\s*([^\n\r]+)', chunk)
        if medeni_match:
            record['MedeniDurum'] = normalize_turkish_text(medeni_match.group(1).strip())
            record['Cinsiyet'] = normalize_turkish_text(medeni_match.group(2).strip())

        records.append(record)

    return records


# ========== TELEGRAM CLIENT MANAGEMENT ==========

async def get_or_create_client():
    """Thread-safe client oluştur veya mevcut client'ı döndür"""
    global client, loop
    
    with client_lock:
        if client is None:
            print("🔄 Creating new Telegram client...")
            if loop is None:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            
            client = TelegramClient(
                StringSession(SESSION_STRING), 
                API_ID, 
                API_HASH,
                loop=loop,
                connection_retries=5,
                retry_delay=2,
                timeout=120,
                auto_reconnect=True
            )
        
        # Client'ı connect et
        if not client.is_connected():
            print("🔗 Connecting Telegram client...")
            await client.connect()
            print("✅ Telegram client connected")
        
        return client


async def cleanup_client():
    """Client'ı güvenli şekilde kapat"""
    global client
    
    with client_lock:
        if client and client.is_connected():
            print("🔌 Disconnecting Telegram client...")
            try:
                await client.disconnect()
                print("✅ Telegram client disconnected")
            except:
                pass
            client = None


async def query_bot_with_command(command: str, timeout: int = 120):
    """
    Query the bot with given command and return raw text.
    Her sorguda yeni client oluşturma, aynı client'ı kullan.
    """
    max_retries = 3
    retry_delay = 2
    
    for retry in range(max_retries):
        try:
            client = await get_or_create_client()
            
            async with client.conversation(BOT_USERNAME, timeout=timeout + 30) as conv:
                print(f"📤 Sending command: {command}")
                await conv.send_message(command)
                
                start_ts = time.time()
                raw_text = ""
                got_file = False
                
                while time.time() - start_ts < timeout:
                    try:
                        response = await conv.get_response(timeout=20)
                    except asyncio.TimeoutError:
                        print("⏳ Timeout waiting for response...")
                        continue
                    
                    text = getattr(response, 'text', '') or ''
                    
                    # "sorgu yapılıyor" gibi mesajları atla
                    if text and any(word in text.lower() for word in ['sorgu yapılıyor', 'işlem devam', 'lütfen bekleyin', 'birazdan', 'yakında']):
                        print("⏳ Sorgu devam ediyor, bekleniyor...")
                        continue
                    
                    # Buton kontrolü - TXT indir butonlarını tıkla
                    if hasattr(response, 'buttons') and response.buttons:
                        print("🔘 Buttons found, checking for download...")
                        for row in response.buttons:
                            for btn in row:
                                btn_text = str(getattr(btn, 'text', '')).lower()
                                if any(keyword in btn_text for keyword in ['txt', 'dosya', '.txt', 'indir', 'download', 'gör', 'aç']):
                                    print(f"📥 Found download button: {btn_text}")
                                    try:
                                        await btn.click()
                                        print("✅ Button clicked, waiting for file...")
                                        # Dosya gelmesini bekle
                                        try:
                                            file_msg = await conv.get_response(timeout=30)
                                        except asyncio.TimeoutError:
                                            print("❌ Timeout waiting for file")
                                            continue
                                        
                                        if file_msg and hasattr(file_msg, 'media') and file_msg.media:
                                            print("📄 Downloading file...")
                                            file_path = await client.download_media(file_msg)
                                            if file_path and os.path.exists(file_path):
                                                try:
                                                    with open(file_path, 'rb') as f:
                                                        content = f.read()
                                                    
                                                    print(f"📊 File size: {len(content)} bytes")
                                                    raw_text = decode_and_fix_text(content)
                                                    got_file = True
                                                    print(f"✅ File downloaded and decoded, size: {len(raw_text)} chars")
                                                    
                                                finally:
                                                    try:
                                                        os.remove(file_path)
                                                    except:
                                                        pass
                                                
                                                if got_file:
                                                    return raw_text
                                    except Exception as e:
                                        print(f"❌ Button click error: {e}")
                                        continue
                    
                    # Eğer mesajın kendisi dosya içeriyorsa
                    if hasattr(response, 'media') and response.media:
                        print("📄 Message has media, downloading...")
                        try:
                            file_path = await client.download_media(response)
                            if file_path and os.path.exists(file_path):
                                with open(file_path, 'rb') as f:
                                    content = f.read()
                                
                                print(f"📊 Media file size: {len(content)} bytes")
                                raw_text = decode_and_fix_text(content)
                                
                                try:
                                    os.remove(file_path)
                                except:
                                    pass
                                
                                return raw_text
                        except Exception as e:
                            print(f"❌ Media download error: {e}")
                    
                    # Eğer text verisi varsa ve anlamlı veri içeriyorsa
                    if text:
                        text = normalize_turkish_text(text)
                        
                        # TC, GSM, Plaka gibi verileri kontrol et
                        if re.search(r'\d{11}', text) or re.search(r'GSM\s*[:=]\s*\d', text) or re.search(r'Plaka\s*[:=]', text, re.IGNORECASE):
                            raw_text = text
                            return raw_text
                        
                        # Boş olmayan ve "sorgu yapılıyor" içermeyen text
                        if text.strip() and not any(word in text.lower() for word in ['sorgu yapılıyor', 'işlem devam']):
                            raw_text = text
                            return raw_text
                    
                    await asyncio.sleep(1)
                
                # Timeout oldu, son mesajı döndür
                if raw_text:
                    return raw_text
                else:
                    return "❌ Sorgu zaman aşımına uğradı veya yanıt alınamadı"
                
        except Exception as e:
            print(f"❌ Query error (attempt {retry + 1}/{max_retries}): {e}")
            
            # Client'ı temizle ve yeniden dene
            await cleanup_client()
            
            if retry < max_retries - 1:
                print(f"🔄 Retrying in {retry_delay} seconds...")
                await asyncio.sleep(retry_delay)
            else:
                return f"Error: {str(e)}"
    
    return "❌ Maximum retry attempts reached"


def sync_query_bot(command: str) -> str:
    """Async query'i sync context'te çalıştır"""
    global loop
    
    try:
        # Her sorguda yeni loop oluşturma
        if loop is None or loop.is_closed():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        # Loop çalışıyor mu kontrol et
        if not loop.is_running():
            result = loop.run_until_complete(query_bot_with_command(command))
        else:
            # Eğer loop çalışıyorsa, run_coroutine_threadsafe kullan
            future = asyncio.run_coroutine_threadsafe(query_bot_with_command(command), loop)
            result = future.result(timeout=180)
        
        return result
        
    except RuntimeError as e:
        print(f"🔄 Runtime error: {e}, creating new loop")
        # Eski loop'u temizle
        try:
            if loop and not loop.is_closed():
                loop.close()
        except:
            pass
        
        # Yeni loop oluştur
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(query_bot_with_command(command))
        
    except Exception as e:
        print(f"❌ Sync query error: {e}")
        return f"Error: {str(e)}"


# ========== ENDPOINT'LER ==========

@app.route('/query', methods=['GET'])
def api_query():
    """Main query endpoint"""
    name = request.args.get('name', '') or request.args.get('first_name', '')
    surname = request.args.get('surname', '') or request.args.get('last_name', '')
    name = name.strip().upper()
    surname = surname.strip().upper()

    if not name or not surname:
        return jsonify({'success': False, 'error': 'name ve surname gerekli'}), 400

    cache_key = f"query_{name}_{surname}"
    if cache_key in result_cache:
        print(f"📦 Cache hit for Query: {name} {surname}")
        return jsonify(result_cache[cache_key])

    command = f"/ad {name} {surname}"
    print(f"🚀 Executing command: {command}")
    raw_text = sync_query_bot(command)
    print(f"📊 Raw response length: {len(raw_text)}")
    
    # Eğer "Error:" ile başlıyorsa, hata mesajını döndür
    if raw_text.startswith("Error:") or raw_text.startswith("❌"):
        result = {
            'success': False,
            'query': f"{name} {surname}",
            'error': raw_text,
            'raw_preview': ""
        }
    else:
        records = extract_simple_records(raw_text)

        if records:
            result = {
                'success': True,
                'query': f"{name} {surname}",
                'count': len(records),
                'records': records,
                'raw_preview': raw_text[:500] if raw_text else ""
            }
        else:
            result = {
                'success': False,
                'query': f"{name} {surname}",
                'error': 'Kayıt bulunamadı',
                'raw_preview': raw_text[:500] if raw_text else ""
            }
    
    result_cache[cache_key] = result
    return jsonify(result)


@app.route('/text', methods=['GET'])
def api_text():
    """Text output endpoint"""
    name = request.args.get('name', '') or request.args.get('first_name', '')
    surname = request.args.get('surname', '') or request.args.get('last_name', '')
    name = name.strip().upper()
    surname = surname.strip().upper()

    if not name or not surname:
        return Response('❌ Hata: name ve surname gerekli', content_type='text/plain; charset=utf-8')

    command = f"/ad {name} {surname}"
    print(f"🚀 Executing command: {command}")
    raw_text = sync_query_bot(command)
    print(f"📊 Raw response length: {len(raw_text)}")
    
    # Eğer hata mesajı varsa
    if raw_text.startswith("Error:") or raw_text.startswith("❌"):
        return Response(f'❌ Hata: {raw_text}', content_type='text/plain; charset=utf-8')
    
    records = extract_simple_records(raw_text)

    if not records:
        return Response(f'❌ {name} {surname} için kayıt bulunamadı.', content_type='text/plain; charset=utf-8')

    lines = []
    lines.append(f"{'='*60}")
    lines.append(f"📋 {name} {surname} - {len(records)} KAYIT")
    lines.append(f"{'='*60}\n")

    for i, rec in enumerate(records, 1):
        lines.append(f"🔸 KAYIT {i}")
        lines.append(f"{'-'*40}")
        if rec['Ad'] or rec['Soyad']:
            lines.append(f"👤 Ad Soyad: {rec['Ad']} {rec['Soyad']}")
        lines.append(f"🪪 TC: {rec['TC']}")
        if rec['DogumYeri'] or rec['DogumTarihi']:
            birth = f"🎂 Doğum: {rec['DogumYeri']}" if rec['DogumYeri'] else "🎂 Doğum: "
            if rec['DogumTarihi']:
                birth += f" / {rec['DogumTarihi']}"
            lines.append(birth)
        if rec['AnneAdi']:
            lines.append(f"👩 Anne: {rec['AnneAdi']}")
        if rec['BabaAdi']:
            lines.append(f"👨 Baba: {rec['BabaAdi']}")
        if rec['Il'] or rec['Ilce']:
            location = []
            if rec['Il']:
                location.append(rec['Il'])
            if rec['Ilce']:
                location.append(rec['Ilce'])
            if location:
                lines.append(f"📍 Yer: {' / '.join(location)}")
        if rec['Telefon']:
            lines.append(f"📱 Telefon: {rec['Telefon']}")
        if rec.get('MedeniDurum'):
            lines.append(f"💍 Medeni Durum: {rec['MedeniDurum']}")
        if rec.get('Cinsiyet'):
            lines.append(f"⚧ Cinsiyet: {rec['Cinsiyet']}")
        lines.append("")

    lines.append(f"{'='*60}")
    lines.append(f"✅ Toplam {len(records)} kayıt listelendi")
    lines.append(f"{'='*60}")

    return Response('\n'.join(lines), content_type='text/plain; charset=utf-8')


# ========== CACHE TEMİZLEME ==========

def cleanup_cache():
    """Eski cache'leri temizle"""
    global result_cache
    
    current_time = time.time()
    keys_to_remove = []
    
    for key, value in result_cache.items():
        if isinstance(value, dict) and 'timestamp' in value:
            # 10 dakikadan eski cache'leri sil
            if current_time - value['timestamp'] > 600:
                keys_to_remove.append(key)
    
    for key in keys_to_remove:
        result_cache.pop(key, None)
    
    if keys_to_remove:
        print(f"🧹 {len(keys_to_remove)} adet eski cache temizlendi")


# Cache timestamp ekleme
def add_to_cache(key, value):
    """Cache'e timestamp ile ekle"""
    result_cache[key] = {
        'data': value,
        'timestamp': time.time()
    }


def get_from_cache(key):
    """Cache'den timestamp kontrolü ile al"""
    if key in result_cache:
        cache_entry = result_cache[key]
        if isinstance(cache_entry, dict) and 'timestamp' in cache_entry:
            # 10 dakikadan eski cache'leri sil
            if time.time() - cache_entry['timestamp'] <= 600:
                return cache_entry['data']
            else:
                # Süresi dolmuş cache'i sil
                result_cache.pop(key, None)
    return None


# Periyodik cache temizleme thread'i
def periodic_cache_cleanup():
    """Periyodik cache temizleme"""
    while True:
        time.sleep(300)  # 5 dakikada bir
        cleanup_cache()

# Thread başlat
cleanup_thread = threading.Thread(target=periodic_cache_cleanup, daemon=True)
cleanup_thread.start()


# ========== UYGULAMA BAŞLATMA/KAPATMA ==========

def init_telegram_client():
    """Uygulama başlangıcında Telegram client'ı başlat"""
    print("🚀 Initializing Telegram client on startup...")
    
    try:
        # Test sorgusu yaparak client'ı başlat
        test_command = "/ad TEST TEST"
        print(f"🔧 Test command: {test_command}")
        result = sync_query_bot(test_command)
        print(f"🔧 Test result length: {len(result)}")
        print("✅ Telegram client initialized successfully")
    except Exception as e:
        print(f"❌ Telegram client initialization failed: {e}")


# Uygulama başlangıcında client'ı başlat
@app.before_first_request
def startup():
    """Uygulama başlangıcında çalışır"""
    print("🎬 Starting up application...")
    # İlk istek geldiğinde client'ı başlat
    init_telegram_client()


# Uygulama kapatıldığında client'ı temizle
import atexit

@atexit.register
def cleanup():
    """Uygulama kapatıldığında çalışır"""
    print("🛑 Cleaning up Telegram client...")
    
    global client, loop
    
    with client_lock:
        if client and client.is_connected():
            try:
                # Sync context'te disconnect et
                if loop and not loop.is_closed():
                    loop.run_until_complete(client.disconnect())
                    print("✅ Telegram client disconnected on exit")
            except:
                pass
            client = None
        
        # Loop'u kapat
        if loop and not loop.is_closed():
            try:
                loop.close()
            except:
                pass
            loop = None


# ========== MAIN ==========
if __name__ == '__main__':
    print('🚀 API Başlatılıyor...')
    print('=' * 60)
    print('🌐 Tarayıcıda aç: http://127.0.0.1:5000')
    print('📊 Query: http://127.0.0.1:5000/query?name=EYMEN&surname=YAVUZ')
    print('📝 Text: http://127.0.0.1:5000/text?name=EYMEN&surname=YAVUZ')
    print('=' * 60)
    
    # Başlangıçta client'ı başlat
    init_telegram_client()
    
    app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False)
