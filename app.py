import os
import re
import time
import html
import json
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# ---------- FUNGSI PEMBERSIH NOMOR ----------
def clean_phone_number(number):
    number = re.sub(r'[^0-9]', '', number)
    if number.startswith('62'):
        number = '0' + number[2:]
    return number

# ---------- FUNGSI CSRF (Mirip di cek.py) ----------
def get_session_and_csrf():
    session = requests.Session()
    user_agent = 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Mobile Safari/537.36'
    try:
        # HEAD request untuk ambil cookie (mirip di cek.py)
        session.head('https://orderkuota.com/', headers={'User-Agent': user_agent}, verify=False)
        phpsessid = session.cookies.get('PHPSESSID', '')
        csrf_token = session.cookies.get('csrf_cookie', '')
        # Tambahkan cookie tambahan (hardcoded dari PHP)
        session.cookies.set('user_id', 'MzAwMDMwMQ%3D%3D')
        session.cookies.set('user_key', '8c96570b661c2e7ed3a4d46fbc432723')
        return session, csrf_token
    except Exception as e:
        return None, None

# ---------- PARSING PAKET (Dari cek.py + perbaikan) ----------
def parse_packages(html_content):
    # Hapus script/style
    clean_res = re.sub(r'<(script|style)[^>]*?>.*?</\1>', '', html_content, flags=re.IGNORECASE | re.DOTALL)
    clean_text = re.sub(r'<[^>]+>', '', clean_res)
    clean_text = html.unescape(clean_text)
    
    # Ekstrak setelah SN/Ref
    sn_match = re.search(r'SN/Ref\s*[:=]?\s*(.*?)(?=rusmanaid|Telp\.|Nominal|Harga|Tanggal|Print|Komplain|Testimonial|$)', clean_text, re.IGNORECASE | re.DOTALL)
    if not sn_match:
        return []
    
    raw_result = sn_match.group(1).strip()
    raw_result = re.sub(r'pesan\s*[:\=].*$', '', raw_result, flags=re.IGNORECASE | re.DOTALL)
    raw_result = re.sub(r'[\r\n\t]+', ' ', raw_result).strip()
    
    packages = [p.strip(" -,;") for p in raw_result.split('|||') if p.strip()]
    
    # Hapus duplikat
    seen = set()
    unique_packages = []
    for p in packages:
        if p not in seen:
            seen.add(p)
            unique_packages.append(p)
    
    # Kita ubah string paket menjadi struktur JSON (mirip seperti di PHP)
    result = []
    for pkg in unique_packages:
        # Coba ekstrak "Nama Paket" dan "Expired" sederhana
        parts = re.split(r'Expired|Exp\.|Exp|Berlaku s/d', pkg, flags=re.I)
        if len(parts) >= 2:
            name = parts[0].strip(' -.,')
            expiry_raw = parts[1].strip()
            # Ambil tanggal expired
            expiry_match = re.search(r'([\d\-\/[A-Za-z]+\s*[\d\:]*)', expiry_raw)
            expiry = expiry_match.group(1) if expiry_match else '-'
            benefit_str = re.sub(r'^[\d\-\/[A-Za-z]+\s*[\d\:]*', '', expiry_raw).strip()
        else:
            name = 'Paket Utama'
            expiry = '-'
            benefit_str = pkg
        
        # Bersihkan nama dari "Dukcapil" dll
        name = re.sub(r'^(?:Dukcapil\s*=\s*Registered|Registered)\s*[\/\-]?\s*', '', name, flags=re.I)
        name = re.sub(r'^\d+\.\s*', '', name)
        
        # Parsing benefit (mirip di cek.py)
        benefits = []
        # Split berdasarkan "DATA" atau koma
        benefit_items = re.split(r'(?=DATA\s)|,', benefit_str)
        for b in benefit_items:
            b = b.strip(' -,')
            if not b:
                continue
            # Cari format "Nama 123 GB"
            m = re.match(r'^(?:DATA\s+)?(.*?)\s+([\d\.]+\s*(?:GB|MB|KB|TB))$', b, re.I)
            if m:
                bname = m.group(1).strip() or 'Kuota Internet'
                remaining = m.group(2).strip()
                benefits.append({'name': bname, 'remaining': remaining})
            else:
                # Cari format "123 Menit Nelpon"
                m2 = re.match(r'([\d\.]+\s*(?:Menit|SMS))\s+(.*)', b, re.I)
                if m2:
                    benefits.append({'name': m2.group(2).strip(), 'remaining': m2.group(1).strip()})
                else:
                    # fallback
                    bclean = re.sub(r'DATA', '', b, flags=re.I).strip()
                    if bclean:
                        benefits.append({'name': bclean, 'remaining': ''})
        if not benefits and benefit_str:
            benefits.append({'name': benefit_str, 'remaining': ''})
        
        result.append({
            'name': name,
            'expiry': expiry,
            'benefits': benefits
        })
    
    return result

# ---------- ROUTE HOME ----------
@app.route('/')
def home():
    return "API Cek Kuota berjalan! (Versi dari cek.py)"

# ---------- ROUTE CEK KUOTA ----------
@app.route('/cek_kuota', methods=['POST'])
def cek_kuota():
    try:
        # Ambil parameter
        no_raw = request.form.get('no', '')
        id_op = request.form.get('id', '')
        if not no_raw or not id_op:
            return jsonify({'status': 'error', 'message': 'Parameter no dan id wajib diisi'}), 400
        
        no = clean_phone_number(no_raw)
        parts = id_op.split(',')
        if len(parts) < 3:
            return jsonify({'status': 'error', 'message': 'Format id tidak valid'}), 400
        ids, prd, op = parts[0], parts[1], parts[2]
        
        # Dapatkan session dan CSRF
        session, csrf_token = get_session_and_csrf()
        if not session or not csrf_token:
            return jsonify({'status': 'fail', 'message': 'Gagal mendapatkan CSRF'}), 500
        
        # Siapkan POST data
        user_agent = 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Mobile Safari/537.36'
        headers = {
            'User-Agent': user_agent,
            'Cookie': f'PHPSESSID={session.cookies.get("PHPSESSID", "")}; user_id=MzAwMDMwMQ%3D%3D; user_key=8c96570b661c2e7ed3a4d46fbc432723; csrf_cookie={csrf_token}',
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        
        payload = {
            'csrf_token': csrf_token,
            'nomor_hp': '083879017166',  # hardcoded
            'pembayaran': 'balance',
            'produk': prd,
            'operator': op,
            'voucher': ids,
            'id_plgn': no,
            'json_format': 1
        }
        
        # POST ke cetak_voucher
        resp = session.post('https://orderkuota.com/cetak_voucher', data=payload, headers=headers, verify=False, timeout=60)
        res_text = resp.text
        
        # Cek error
        if "Sedang rekap dan pembukuan" in res_text:
            return jsonify({'status': 'fail', 'message': '⏳ Layanan sedang rekap data (23.40-00.10), coba lagi nanti.'})
        
        if "errors" in res_text.lower():
            try:
                err_msg = resp.json()['errors'][0]
            except:
                err_msg = "Terjadi kesalahan (limit atau IP)."
            return jsonify({'status': 'fail', 'message': err_msg})
        
        if "success" not in res_text.lower():
            return jsonify({'status': 'fail', 'message': 'Layanan sedang maintenance (Server Down).'})
        
        # Ambil ID transaksi
        try:
            id_trx = resp.json().get('id', '')
        except:
            return jsonify({'status': 'fail', 'message': 'Gagal mengekstrak ID transaksi.'})
        
        # Delay 5 detik
        time.sleep(5)
        session.get(f"https://orderkuota.com/cek-status/trx/{id_trx}", headers=headers, verify=False, timeout=30)
        
        # Delay 7 detik
        time.sleep(7)
        view_resp = session.get(f"https://orderkuota.com/akun/riwayat-transaksi/view/{id_trx}", headers=headers, verify=False, timeout=30)
        view_text = view_resp.text
        
        # Cek berbagai kondisi error
        if "Anda telah mencapai batas maksimal" in view_text or "Nomor Tujuan Tidak Dapat di Proses" in view_text:
            return jsonify({'status': 'fail', 'message': '⏳ Tunggu 3 jam untuk cek lagi.'})
        if "Nomor ini belum memiliki Paket" in view_text:
            return jsonify({'status': 'fail', 'message': '❌ Nomor belum punya paket.'})
        if re.search(r'(Stts Beli\s*:\s*Gagal|Status Pengisian\s*RF|Refund|Cek kembali nomor tujuan)', view_text, re.I):
            return jsonify({'status': 'fail', 'message': '❌ Gagal/Refund.'})
        if "SN/Ref" not in view_text:
            return jsonify({'status': 'fail', 'message': '❌ Gagal ambil data.'})
        
        # Parsing paket
        packages = parse_packages(view_text)
        
        # Kirim sukses
        return jsonify({
            'status': 'success',
            'message': '✅ Cek Berhasil!',
            'data': {
                'nomor': no,
                'packages': packages
            }
        })
        
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

# ---------- MAIN ----------
if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
