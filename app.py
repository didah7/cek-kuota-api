import os
import re
import time
import json
import hashlib
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

def clean_phone_number(number):
    number = re.sub(r'[^0-9]', '', number)
    if number.startswith('62'):
        number = '0' + number[2:]
    return number

def get_csrf_and_phpsessid():
    session = requests.Session()
    headers = {
        'User-Agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Mobile Safari/537.36'
    }
    try:
        resp = session.get('https://orderkuota.com/', headers=headers, timeout=30)
        csrf_token = session.cookies.get('csrf_cookie', '')
        phpsessid = session.cookies.get('PHPSESSID', '')
        return session, csrf_token, phpsessid
    except Exception as e:
        print(f"[ERROR] Gagal ambil CSRF: {e}")
        return None, '', ''

def parse_packages(html_content):
    # Hapus script & style
    cleaned = re.sub(r'<(script|style)[^>]*?>.*?</\1>', '', html_content, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r'<[^>]+>', ' ', cleaned)
    cleaned = re.sub(r'&[a-zA-Z]+;', lambda m: {'&nbsp;': ' '}.get(m.group(0), m.group(0)), cleaned)

    # Ambil bagian setelah "SN/Ref"
    sn_match = re.search(r'SN/Ref\s*[:=]?\s*(.*?)(?=rusmanaid|Telp\.|Nominal|Harga|Tanggal|Print|Komplain|Testimonial|$)', cleaned, re.DOTALL | re.IGNORECASE)
    if sn_match:
        text = sn_match.group(1).strip()
    else:
        text = cleaned.strip()

    text = re.sub(r'pesan\s*[:\=].*$', '', text, flags=re.IGNORECASE)
    text = re.sub(r'[\r\n\t]+', ' ', text)

    packages_raw = text.split('|||')
    if len(packages_raw) <= 1:
        packages_raw = [text]

    packages = []
    seen = set()

    for pkg in packages_raw:
        pkg = pkg.strip(' -,;')
        if not pkg:
            continue
        pkg_hash = hashlib.md5(pkg.encode()).hexdigest()
        if pkg_hash in seen:
            continue
        seen.add(pkg_hash)

        # Skip "Sisa Pulsa"
        if re.search(r'Sisa Pulsa.*?Rp\s*[\d\.\,]+', pkg, re.IGNORECASE):
            continue

        # --- TELKOMSEL ---
        if re.search(r'Masa Aktif Kartu|Info Paket Aktif', pkg, re.IGNORECASE):
            expiry_match = re.search(r'Masa Aktif Kartu\s*:\s*([0-9\sA-Za-z]+)(?:\s*-)', pkg, re.IGNORECASE)
            expiry = expiry_match.group(1).strip() if expiry_match else '-'
            reg_match = re.search(r'Status Registrasi\s*:\s*([a-zA-Z\s]+)(?:\s*-)', pkg, re.IGNORECASE)
            reg_status = reg_match.group(1).strip() if reg_match else ''

            paket_section = re.split(r'Info Paket Aktif\s*:', pkg, flags=re.IGNORECASE)
            if len(paket_section) > 1:
                paket_text = paket_section[1]
                items = re.split(r'#\d+\s*-?\s*', paket_text)
                for item in items:
                    item = item.strip(' -.#')
                    if not item:
                        continue
                    exp_match = re.search(r'aktif hingga\s*([^#\.]+)', item, re.IGNORECASE)
                    exp = exp_match.group(1).strip() if exp_match else '-'
                    if exp_match:
                        item = re.sub(r'aktif hingga\s*[^#\.]+', '', item, flags=re.IGNORECASE)
                    quota_match = re.search(r'([\d\.]+\s*(?:GB|MB|KB|TB))\s*\/\s*([\d\.]+\s*(?:GB|MB|KB|TB))', item, re.IGNORECASE)
                    if quota_match:
                        remaining = quota_match.group(0).strip()
                        item = re.sub(r'[\d\.]+\s*(?:GB|MB|KB|TB)\s*\/\s*[\d\.]+\s*(?:GB|MB|KB|TB)', '', item)
                    else:
                        remaining = ''
                    name = item.strip(' -/')
                    if not name:
                        name = 'Paket Internet'
                    benefits = []
                    if remaining:
                        benefits.append({'name': 'Kuota Internet', 'remaining': remaining})
                    packages.append({
                        'name': name,
                        'expiry': exp if exp != '-' else '',
                        'benefits': benefits,
                        'extra': {'registrasi': reg_status, 'masa_aktif_kartu': expiry}
                    })
            continue

        # --- INDOSAT / TRI ---
        if re.search(r'Sisa Kuota\s*:', pkg, re.IGNORECASE):
            parts = re.split(r'Sisa Kuota\s*:', pkg, flags=re.IGNORECASE)
            left_part = parts[0].strip(' /,-')
            right_part = parts[1].strip() if len(parts) > 1 else ''
            expiry_match = re.search(r'\((.*?)\)', left_part)
            expiry = expiry_match.group(1).strip() if expiry_match else '-'
            name = re.sub(r'\(.*?\)', '', left_part).strip()
            if not name:
                name = 'Paket Data'
            benefit_items = []
            for b in right_part.split(','):
                b = b.strip()
                if not b:
                    continue
                m = re.match(r'^(.*?)\s*-\s*(.*?)\s*-\s*(?:Exp|Expired)\s*:(.*)$', b, re.IGNORECASE)
                if m:
                    benefit_items.append({
                        'name': m.group(2).strip(),
                        'remaining': m.group(1).strip(),
                        'expiry': m.group(3).strip()
                    })
                else:
                    benefit_items.append({'name': b, 'remaining': ''})
            packages.append({
                'name': name,
                'expiry': expiry,
                'benefits': benefit_items
            })
            continue

        # --- UMUM (XL, Axis, Smartfren) ---
        general_match = re.match(r'^(.*?)(?:Expired|Exp\.|Exp|Berlaku s\/d)\s*[:\=]?\s*([\d\-\/[A-Za-z]+\s*[\d\:]*)(.*)$', pkg, re.IGNORECASE)
        if general_match:
            name = general_match.group(1).strip(' -.,')
            expiry = general_match.group(2).strip()
            benefit_str = general_match.group(3).strip(' -.,')
        else:
            name = 'Paket Utama'
            expiry = '-'
            benefit_str = pkg

        name = re.sub(r'^(?:Dukcapil\s*=\s*Registered|Registered)\s*[\/\-]?\s*', '', name, flags=re.IGNORECASE)
        name = re.sub(r'^\d+\.\s*', '', name)

        benefit_items = []
        parts_ben = re.split(r'(?=DATA\s)|,', benefit_str)
        for b in parts_ben:
            b = b.strip(' -,')
            if not b:
                continue
            m = re.match(r'^(?:DATA\s+)?(.*?)\s+([\d\.]+\s*(?:GB|MB|KB|TB))$', b, re.IGNORECASE)
            if m:
                bname = m.group(1).strip()
                if not bname:
                    bname = 'Kuota Internet'
                remaining = m.group(2).strip()
                benefit_items.append({'name': bname, 'remaining': remaining})
            else:
                m2 = re.match(r'([\d\.]+\s*(?:Menit|SMS))\s+(.*)', b, re.IGNORECASE)
                if m2:
                    benefit_items.append({'name': m2.group(2).strip(), 'remaining': m2.group(1).strip()})
                else:
                    b_clean = re.sub(r'DATA', '', b, flags=re.IGNORECASE).strip()
                    if b_clean:
                        odd_match = re.match(r'(.*?)\s+(\d+)\s+0$', b_clean)
                        if odd_match:
                            benefit_items.append({'name': odd_match.group(1).strip(), 'remaining': odd_match.group(2) + ' GB'})
                        else:
                            benefit_items.append({'name': b_clean, 'remaining': ''})

        if not benefit_items and benefit_str:
            benefit_items.append({'name': benefit_str, 'remaining': ''})

        packages.append({
            'name': name,
            'expiry': expiry,
            'benefits': benefit_items
        })

    return packages


@app.route('/')
def home():
    return "API Cek Kuota berjalan!"


@app.route('/cek_kuota', methods=['POST'])
def cek_kuota():
    try:
        nomor = request.form.get('no', '')
        id_operator = request.form.get('id', '')
        if not nomor or not id_operator:
            return jsonify({'status': 'error', 'message': 'Parameter no dan id wajib diisi'}), 400

        no = clean_phone_number(nomor)
        parts = id_operator.split(',')
        if len(parts) < 3:
            return jsonify({'status': 'error', 'message': 'Format id operator tidak valid'}), 400
        voucher, produk, operator_id = parts[0], parts[1], parts[2]

        # Ambil CSRF
        session, csrf_token, phpsessid = get_csrf_and_phpsessid()
        if not session or not csrf_token or not phpsessid:
            return jsonify({'status': 'fail', 'message': 'Gagal mendapatkan token CSRF'}), 400

        # Set cookie tambahan
        session.cookies.set('user_id', 'MzAwMDMwMQ%3D%3D')
        session.cookies.set('user_key', '8c96570b661c2e7ed3a4d46fbc432723')

        headers_post = {
            'User-Agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Mobile Safari/537.36',
            'Referer': 'https://orderkuota.com/',
            'Origin': 'https://orderkuota.com',
            'X-Requested-With': 'XMLHttpRequest'
        }

        data = {
            'csrf_token': csrf_token,
            'nomor_hp': '083879017166',
            'pembayaran': 'balance',
            'produk': produk,
            'operator': operator_id,
            'voucher': voucher,
            'id_plgn': no,
            'json_format': '1'
        }

        resp_post = session.post('https://orderkuota.com/cetak_voucher', data=data, headers=headers_post, timeout=60)
        try:
            result = resp_post.json()
        except:
            return jsonify({'status': 'error', 'message': 'Respon tidak valid JSON'}), 500

        if 'errors' in result:
            error_msg = result['errors'][0] if isinstance(result['errors'], list) else result['errors']
            return jsonify({'status': 'fail', 'message': error_msg})

        if re.search(r'Sedang rekap dan pembukuan data harian pukul 23.40 - 00.10', resp_post.text, re.IGNORECASE):
            return jsonify({'status': 'fail', 'message': '⏳ Layanan sedang rekap data (23.40–00.10), coba lagi nanti.'})

        if 'success' not in result:
            return jsonify({'status': 'fail', 'message': 'Gagal memproses permintaan'})

        id_trx = result.get('id', '')
        if not id_trx:
            return jsonify({'status': 'fail', 'message': 'ID transaksi tidak ditemukan'})

        time.sleep(5)
        session.get(f'https://orderkuota.com/cek-status/trx/{id_trx}', headers=headers_post, timeout=30)
        time.sleep(7)

        detail_resp = session.get(f'https://orderkuota.com/akun/riwayat-transaksi/view/{id_trx}', headers=headers_post, timeout=30)
        detail_html = detail_resp.text

        if re.search(r'Anda telah mencapai batas maksimal pengecekan', detail_html, re.IGNORECASE):
            return jsonify({'status': 'fail', 'message': '⏳ Silahkan tunggu 3 jam untuk cek kuota lagi dengan nomor yang sama.'})
        if re.search(r'Nomor Tujuan Tidak Dapat di Proses', detail_html, re.IGNORECASE):
            return jsonify({'status': 'fail', 'message': '⏳ Silahkan tunggu 3 jam untuk cek kuota lagi dengan nomor yang sama.'})
        if re.search(r'Nomor ini belum memiliki Paket', detail_html, re.IGNORECASE):
            return jsonify({'status': 'fail', 'message': '❌ Nomor ini belum memiliki paket kuota.'})
        if re.search(r'(Stts Beli\s*:\s*Gagal|Status Pengisian\s*RF|Refund|Cek kembali nomor tujuan)', detail_html, re.IGNORECASE):
            return jsonify({'status': 'fail', 'message': '❌ Pengecekan gagal: Nomor tidak valid atau sedang gangguan (Refund).'})
        if not re.search(r'SN/Ref', detail_html, re.IGNORECASE):
            return jsonify({'status': 'fail', 'message': '❌ Gagal mengambil data, coba lagi nanti.'})

        packages = parse_packages(detail_html)

        return jsonify({
            'status': 'success',
            'message': '✅ Cek Kuota Berhasil!',
            'data': {
                'nomor': no,
                'packages': packages
            }
        })

    except Exception as e:
        print(f"[EXCEPTION] {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
