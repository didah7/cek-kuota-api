import os
import re
import time
import json
import html
import urllib3
import requests
from flask import Flask, request, jsonify

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)

def clean_phone_number(number):
    number = re.sub(r'[^0-9]', '', number)
    if number.startswith('62'):
        number = '0' + number[2:]
    return number

def parse_packages_from_html(html_content):
    clean_res = re.sub(r'<(script|style)[^>]*?>.*?</\1>', '', html_content, flags=re.IGNORECASE | re.DOTALL)
    clean_text = re.sub(r'<[^>]+>', '', clean_res)
    clean_text = html.unescape(clean_text)

    sn_match = re.search(r'SN/Ref\s*[:=]?\s*(.*?)(?=rusmanaid|Telp\.|Nominal|Harga|Tanggal|Print|Komplain|Testimonial|$)', 
                         clean_text, re.IGNORECASE | re.DOTALL)
    if not sn_match:
        return []

    raw_result = sn_match.group(1).strip()
    raw_result = re.sub(r'pesan\s*[:\=].*$', '', raw_result, flags=re.IGNORECASE | re.DOTALL)
    raw_result = re.sub(r'[\r\n\t]+', ' ', raw_result).strip()

    packages_raw = [p.strip(" -,;") for p in raw_result.split('|||') if p.strip()]

    seen = set()
    packages_uniq = []
    for p in packages_raw:
        if p not in seen:
            seen.add(p)
            packages_uniq.append(p)

    return packages_uniq

@app.route('/')
def home():
    return 'API Cek Kuota (berbasis cek.py) berjalan!'

@app.route('/cek_kuota', methods=['POST'])
def cek_kuota():
    try:
        no_raw = request.form.get('no', '')
        id_op = request.form.get('id', '')
        if not no_raw or not id_op:
            return jsonify({'status': 'error', 'message': 'Parameter no dan id wajib diisi'}), 400

        no = clean_phone_number(no_raw)
        ids, prd, op = id_op.split(',')

        session = requests.Session()
        user_agent = 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Mobile Safari/537.36'

        # Gunakan GET, bukan HEAD, untuk mendapatkan cookie
        try:
            # Tambahkan header secukupnya
            get_headers = {
                'User-Agent': user_agent,
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1'
            }
            resp_get = session.get('https://orderkuota.com/', headers=get_headers, verify=False, timeout=30)
            # Ambil cookie
            phpsessid = session.cookies.get('PHPSESSID', '')
            csrf_cookie = session.cookies.get('csrf_cookie', '')
            # Debug ke log
            print(f"[DEBUG] Status: {resp_get.status_code}")
            print(f"[DEBUG] PHPSESSID: {phpsessid}, CSRF: {csrf_cookie}")
            print(f"[DEBUG] All cookies: {session.cookies.get_dict()}")
        except Exception as e:
            print(f"[ERROR] Gagal ambil token: {e}")
            return jsonify({'status': 'fail', 'message': f'Gagal ambil token: {str(e)}'}), 500

        if not csrf_cookie:
            # Jika csrf_cookie tetap kosong, coba cari di response text (misal di meta)
            # Ini alternatif jika cookie tidak ada
            match = re.search(r'name="csrf_token"\s+value="([^"]+)"', resp_get.text)
            if match:
                csrf_cookie = match.group(1)
                print(f"[DEBUG] CSRF ditemukan di HTML: {csrf_cookie}")
            else:
                print("[ERROR] CSRF tidak ditemukan di cookie maupun HTML")
                return jsonify({'status': 'fail', 'message': 'Gagal mendapatkan CSRF token'}), 500

        headers = {
            'User-Agent': user_agent,
            'Cookie': f'PHPSESSID={phpsessid}; user_id=MzAwMDMwMQ%3D%3D; user_key=8c96570b661c2e7ed3a4d46fbc432723; csrf_cookie={csrf_cookie}',
            'Content-Type': 'application/x-www-form-urlencoded'
        }

        payload = {
            'csrf_token': csrf_cookie,
            'nomor_hp': '083879017166',
            'pembayaran': 'balance',
            'produk': prd,
            'operator': op,
            'voucher': ids,
            'id_plgn': no,
            'json_format': 1
        }

        req1 = session.post("https://orderkuota.com/cetak_voucher", data=payload, headers=headers, verify=False, timeout=60)
        res1 = req1.text

        if "Sedang rekap dan pembukuan" in res1:
            return jsonify({'status': 'fail', 'message': '⏳ Layanan sedang rekap data (23.40–00.10), coba lagi nanti.'})

        if "errors" in res1.lower():
            try:
                err_msg = req1.json()['errors'][0]
            except:
                err_msg = "Terjadi kesalahan (silakan cek limit atau IP)."
            return jsonify({'status': 'fail', 'message': err_msg})

        if "success" not in res1.lower():
            return jsonify({'status': 'fail', 'message': 'Gagal memproses permintaan (success tidak ditemukan).'})

        try:
            id_trx = req1.json().get('id', '')
        except:
            return jsonify({'status': 'fail', 'message': 'Gagal mengekstrak ID Transaksi.'})

        time.sleep(5)
        session.get(f"https://orderkuota.com/cek-status/trx/{id_trx}", headers=headers, verify=False, timeout=30)
        time.sleep(7)

        req_view = session.get(f"https://orderkuota.com/akun/riwayat-transaksi/view/{id_trx}", headers=headers, verify=False, timeout=30)
        res_view = req_view.text

        if "Anda telah mencapai batas maksimal" in res_view or "Nomor Tujuan Tidak Dapat di Proses" in res_view:
            return jsonify({'status': 'fail', 'message': '⏳ Silahkan tunggu 3 jam untuk cek kuota lagi dengan nomor yang sama.'})
        if "Nomor ini belum memiliki Paket" in res_view:
            return jsonify({'status': 'fail', 'message': '❌ Nomor ini belum memiliki paket kuota.'})
        if re.search(r'(Stts Beli\s*:\s*Gagal|Status Pengisian\s*RF|Refund|Cek kembali nomor tujuan)', res_view, re.IGNORECASE):
            return jsonify({'status': 'fail', 'message': '❌ Pengecekan gagal: Nomor tidak valid atau gangguan dari operator.'})
        if "SN/Ref" not in res_view:
            return jsonify({'status': 'fail', 'message': '❌ Gagal mengambil rincian (SN/Ref tidak ditemukan).'})

        packages = parse_packages_from_html(res_view)
        return jsonify({
            'status': 'success',
            'message': '✅ Cek Kuota Berhasil!',
            'data': {'nomor': no, 'packages': packages}
        })

    except Exception as e:
        print(f"[EXCEPTION] {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
