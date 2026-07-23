import os, re, time, json, hashlib, requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# ---------- FUNGSI PEMBERSIH NOMOR ----------
def clean_phone_number(n):
    n = re.sub(r'\D', '', n)
    if n.startswith('62'):
        n = '0' + n[2:]
    return n

# ---------- AMBIL CSRF & SESSION ----------
def get_session_and_csrf():
    s = requests.Session()
    s.get('https://orderkuota.com/', headers={'User-Agent': 'Mozilla/5.0 (Linux; Android 10; K) Chrome/121'})
    csrf = s.cookies.get('csrf_cookie', '')
    return s, csrf

# ---------- PARSING PAKET (LENGKAP) ----------
def parse_packages(html):
    # Hapus script & style
    cleaned = re.sub(r'<(script|style)[^>]*?>.*?</\1>', '', html, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r'<[^>]+>', ' ', cleaned)
    cleaned = re.sub(r'&[a-zA-Z]+;', lambda m: {'&nbsp;':' '}.get(m.group(0), m.group(0)), cleaned)
    
    # Ambil setelah "SN/Ref"
    sn_match = re.search(r'SN/Ref\s*[:=]?\s*(.*?)(?=rusmanaid|Telp\.|Nominal|Harga|Tanggal|Print|Komplain|Testimonial|$)', cleaned, re.DOTALL | re.IGNORECASE)
    text = sn_match.group(1).strip() if sn_match else cleaned.strip()
    text = re.sub(r'pesan\s*[:\=].*$', '', text, flags=re.IGNORECASE)
    text = re.sub(r'[\r\n\t]+', ' ', text)
    
    raw = text.split('|||')
    if len(raw) <= 1:
        raw = [text]
    
    packages = []
    seen = set()
    for pkg in raw:
        pkg = pkg.strip(' -,;')
        if not pkg or re.search(r'Sisa Pulsa', pkg, re.I):
            continue
        h = hashlib.md5(pkg.encode()).hexdigest()
        if h in seen: continue
        seen.add(h)
        
        # --- Telkomsel ---
        if re.search(r'Masa Aktif Kartu|Info Paket Aktif', pkg, re.I):
            expiry_match = re.search(r'Masa Aktif Kartu\s*:\s*([0-9\sA-Za-z]+)(?:\s*-)', pkg, re.I)
            expiry = expiry_match.group(1).strip() if expiry_match else '-'
            reg_match = re.search(r'Status Registrasi\s*:\s*([a-zA-Z\s]+)(?:\s*-)', pkg, re.I)
            reg_status = reg_match.group(1).strip() if reg_match else ''
            
            paket_section = re.split(r'Info Paket Aktif\s*:', pkg, flags=re.I)
            if len(paket_section) > 1:
                items = re.split(r'#\d+\s*-?\s*', paket_section[1])
                for item in items:
                    item = item.strip(' -.#')
                    if not item: continue
                    exp_match = re.search(r'aktif hingga\s*([^#\.]+)', item, re.I)
                    exp = exp_match.group(1).strip() if exp_match else '-'
                    if exp_match:
                        item = re.sub(r'aktif hingga\s*[^#\.]+', '', item, flags=re.I)
                    quota_match = re.search(r'([\d\.]+\s*(?:GB|MB|KB|TB))\s*\/\s*([\d\.]+\s*(?:GB|MB|KB|TB))', item, re.I)
                    remaining = quota_match.group(0).strip() if quota_match else ''
                    if quota_match:
                        item = re.sub(r'[\d\.]+\s*(?:GB|MB|KB|TB)\s*\/\s*[\d\.]+\s*(?:GB|MB|KB|TB)', '', item)
                    name = item.strip(' -/') or 'Paket Internet'
                    benefits = [{'name':'Kuota Internet','remaining':remaining}] if remaining else []
                    packages.append({'name':name,'expiry':exp,'benefits':benefits,'extra':{'registrasi':reg_status,'masa_aktif_kartu':expiry}})
            continue
        
        # --- Indosat / Tri ---
        if re.search(r'Sisa Kuota\s*:', pkg, re.I):
            parts = re.split(r'Sisa Kuota\s*:', pkg, flags=re.I)
            left = parts[0].strip(' /,-')
            right = parts[1].strip() if len(parts)>1 else ''
            exp_match = re.search(r'\((.*?)\)', left)
            expiry = exp_match.group(1).strip() if exp_match else '-'
            name = re.sub(r'\(.*?\)', '', left).strip() or 'Paket Data'
            benefits = []
            for b in right.split(','):
                b = b.strip()
                if not b: continue
                m = re.match(r'^(.*?)\s*-\s*(.*?)\s*-\s*(?:Exp|Expired)\s*:(.*)$', b, re.I)
                if m:
                    benefits.append({'name': m.group(2).strip(), 'remaining': m.group(1).strip(), 'expiry': m.group(3).strip()})
                else:
                    benefits.append({'name': b, 'remaining': ''})
            packages.append({'name':name,'expiry':expiry,'benefits':benefits})
            continue
        
        # --- Umum (XL, Axis, Smartfren) ---
        gmatch = re.match(r'^(.*?)(?:Expired|Exp\.|Exp|Berlaku s\/d)\s*[:\=]?\s*([\d\-\/[A-Za-z]+\s*[\d\:]*)(.*)$', pkg, re.I)
        if gmatch:
            name = gmatch.group(1).strip(' -.,')
            expiry = gmatch.group(2).strip()
            benefit_str = gmatch.group(3).strip(' -.,')
        else:
            name, expiry, benefit_str = 'Paket Utama', '-', pkg
        name = re.sub(r'^(?:Dukcapil\s*=\s*Registered|Registered)\s*[\/\-]?\s*', '', name, flags=re.I)
        name = re.sub(r'^\d+\.\s*', '', name)
        
        benefits = []
        for b in re.split(r'(?=DATA\s)|,', benefit_str):
            b = b.strip(' -,')
            if not b: continue
            m = re.match(r'^(?:DATA\s+)?(.*?)\s+([\d\.]+\s*(?:GB|MB|KB|TB))$', b, re.I)
            if m:
                bname = m.group(1).strip() or 'Kuota Internet'
                benefits.append({'name': bname, 'remaining': m.group(2).strip()})
            else:
                m2 = re.match(r'([\d\.]+\s*(?:Menit|SMS))\s+(.*)', b, re.I)
                if m2:
                    benefits.append({'name': m2.group(2).strip(), 'remaining': m2.group(1).strip()})
                else:
                    bclean = re.sub(r'DATA', '', b, flags=re.I).strip()
                    if bclean:
                        odd = re.match(r'(.*?)\s+(\d+)\s+0$', bclean)
                        if odd:
                            benefits.append({'name': odd.group(1).strip(), 'remaining': odd.group(2)+' GB'})
                        else:
                            benefits.append({'name': bclean, 'remaining': ''})
        if not benefits and benefit_str:
            benefits.append({'name': benefit_str, 'remaining': ''})
        packages.append({'name':name,'expiry':expiry,'benefits':benefits})
    
    return packages

# ---------- ROUTE HOME ----------
@app.route('/')
def home():
    return 'API Cek Kuota berjalan!'

# ---------- ROUTE CEK KUOTA ----------
@app.route('/cek_kuota', methods=['POST'])
def cek_kuota():
    try:
        no_raw = request.form.get('no', '')
        id_op = request.form.get('id', '')
        if not no_raw or not id_op:
            return jsonify({'status':'error','message':'Parameter no dan id wajib diisi'}), 400
        
        no = clean_phone_number(no_raw)
        parts = id_op.split(',')
        if len(parts) < 3:
            return jsonify({'status':'error','message':'Format id tidak valid'}), 400
        voucher, produk, operator = parts[0], parts[1], parts[2]

        session, csrf = get_session_and_csrf()
        if not csrf:
            return jsonify({'status':'fail','message':'Gagal dapat CSRF'}), 400

        session.cookies.set('user_id', 'MzAwMDMwMQ%3D%3D')
        session.cookies.set('user_key', '8c96570b661c2e7ed3a4d46fbc432723')

        data = {
            'csrf_token': csrf,
            'nomor_hp': '083879017166',
            'pembayaran': 'balance',
            'produk': produk,
            'operator': operator,
            'voucher': voucher,
            'id_plgn': no,
            'json_format': '1'
        }
        headers = {
            'User-Agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Mobile Safari/537.36',
            'Referer': 'https://orderkuota.com/',
            'X-Requested-With': 'XMLHttpRequest'
        }
        resp = session.post('https://orderkuota.com/cetak_voucher', data=data, headers=headers, timeout=60)
        try:
            res = resp.json()
        except:
            return jsonify({'status':'fail','message':'Respon bukan JSON'}), 500

        if 'errors' in res:
            err = res['errors'][0] if isinstance(res['errors'], list) else res['errors']
            return jsonify({'status':'fail','message':err})
        if 'success' not in res:
            return jsonify({'status':'fail','message':'Gagal memproses permintaan'})

        id_trx = res.get('id')
        if not id_trx:
            return jsonify({'status':'fail','message':'ID transaksi tidak ditemukan'})

        time.sleep(5)
        session.get(f'https://orderkuota.com/cek-status/trx/{id_trx}', headers=headers)
        time.sleep(7)
        det = session.get(f'https://orderkuota.com/akun/riwayat-transaksi/view/{id_trx}', headers=headers)
        html = det.text

        # Cek kondisi error
        if re.search(r'(Anda telah mencapai batas maksimal|Nomor Tujuan Tidak Dapat di Proses)', html, re.I):
            return jsonify({'status':'fail','message':'⏳ Tunggu 3 jam untuk cek lagi'})
        if re.search(r'Nomor ini belum memiliki Paket', html, re.I):
            return jsonify({'status':'fail','message':'❌ Nomor belum punya paket'})
        if re.search(r'(Stts Beli\s*:\s*Gagal|Refund)', html, re.I):
            return jsonify({'status':'fail','message':'❌ Gagal/Refund'})
        if not re.search(r'SN/Ref', html, re.I):
            return jsonify({'status':'fail','message':'❌ Gagal ambil data'})

        packages = parse_packages(html)
        return jsonify({
            'status':'success',
            'message':'✅ Cek Berhasil!',
            'data': {'nomor': no, 'packages': packages}
        })
    except Exception as e:
        return jsonify({'status':'error','message':str(e)}), 500

# ---------- RUN ----------
if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
