from flask import Flask, jsonify, request, render_template, session, redirect, url_for
from urllib.parse import urlparse, urljoin
from functools import wraps
import json
import math
import os
import urllib.request
from datetime import datetime, timedelta, timezone

# app.py はプロジェクト直下に置く。
# 実体（templates / static / data）は bousai_app/ 配下にあるので、そこを参照する。
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(BASE_DIR, 'bousai_app')

app = Flask(
    __name__,
    template_folder=os.path.join(APP_DIR, 'templates'),
    static_folder=os.path.join(APP_DIR, 'static'),
)
app.secret_key = 'your-secret-key-here'

# 管理者認証情報
ADMIN_CREDENTIALS = {
    'admin': '123'
}

# ────────────────────────────────
# 気象警報・注意報設定
PREFECTURE_CODE = "020000"  # 青森県
AREA_NAME = "青森市"

AREA_CODE = "220100"
WARNING_AREA_CODE = f"0{AREA_CODE}"

WARNING_URL = (
    f"https://www.jma.go.jp/bosai/warning/data/r8/{PREFECTURE_CODE}.json"
)

JST = timezone(timedelta(hours=9))

# 警報・注意報のコード一覧
WARNING_CODES = {
    "00": "解除",
    "02": "暴風雪警報",
    "03": "レベル3大雨警報",
    "04": "洪水警報",
    "05": "暴風警報",
    "06": "大雪警報",
    "07": "波浪警報",
    "08": "レベル3高潮警報",
    "09": "レベル3土砂災害警報",
    "10": "レベル2大雨注意報",
    "12": "大雪注意報",
    "13": "風雪注意報",
    "14": "雷注意報",
    "15": "強風注意報",
    "16": "波浪注意報",
    "17": "融雪注意報",
    "18": "洪水注意報",
    "19": "レベル2高潮注意報",
    "20": "濃霧注意報",
    "21": "乾燥注意報",
    "22": "なだれ注意報",
    "23": "低温注意報",
    "24": "霜注意報",
    "25": "着氷注意報",
    "26": "着雪注意報",
    "27": "その他の注意報",
    "29": "レベル2土砂災害注意報",
    "32": "暴風雪特別警報",
    "33": "レベル5大雨特別警報",
    "35": "暴風特別警報",
    "36": "大雪特別警報",
    "37": "波浪特別警報",
    "38": "レベル5高潮特別警報",
    "39": "レベル5土砂災害特別警報",
    "43": "レベル4大雨危険警報",
    "48": "レベル4高潮危険警報",
    "49": "レベル4土砂災害危険警報"
}

HAZARD_TYPES = {
    'earthquake': '地震',
    'flood': '洪水・浸水',
    'landslide': '土砂災害',
    'tsunami': '津波',
    'storm_surge': '高潮',
    'large_fire': '大規模火災',
}
SEARCH_RADII_KM = (5, 10, 20, 50)

# ────────────────────────────────
# サンプルデータの読み込み
DATA_FILE = os.path.join(APP_DIR, 'data', 'shelters.json')
INSTRUCTIONS_FILE = os.path.join(APP_DIR, 'data', 'instructions.json')

def load_json(path, default):
    """JSONファイルを読み込む（存在しない・壊れている場合は default を返す）"""
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default

shelters = load_json(DATA_FILE, [])
instructions = load_json(INSTRUCTIONS_FILE, [])

def save_instructions():
    """指示ボードのデータをファイルに保存する"""
    try:
        with open(INSTRUCTIONS_FILE, 'w', encoding='utf-8') as f:
            json.dump(instructions, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
# ────────────────────────────────

# ────────────────────────────────
# 認証関連の設定とヘルパー関数
def is_safe_url(target):
    """リダイレクト先URLが安全かどうかチェック"""
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc

def login_required(f):
    """認証が必要なページに付けるデコレータ"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            # 現在のURLをnextパラメータとしてログイン画面にリダイレクト
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def get_japan_time():
    """日本時間（JST）の現在時刻を取得する"""
    return datetime.now(JST).strftime("%Y年%m月%d日 %H:%M")


def format_report_time(iso_str):
    """気象庁の発表時刻（ISO形式）をJSTの表示用文字列に変換する"""
    if not iso_str:
        return "不明"
    try:
        parsed = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
        if parsed.tzinfo:
            parsed = parsed.astimezone(JST)
        return parsed.strftime("%Y年%m月%d日 %H:%M")
    except ValueError:
        return iso_str


def calculate_distance_km(start_latitude, start_longitude, end_latitude, end_longitude):
    """2地点間の直線距離をキロメートルで返す"""
    earth_radius_km = 6371
    latitude_delta = math.radians(end_latitude - start_latitude)
    longitude_delta = math.radians(end_longitude - start_longitude)
    haversine = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(math.radians(start_latitude))
        * math.cos(math.radians(end_latitude))
        * math.sin(longitude_delta / 2) ** 2
    )
    return earth_radius_km * 2 * math.atan2(math.sqrt(haversine), math.sqrt(1 - haversine))


def filter_shelters(
    district=None,
    keyword=None,
    hazard=None,
    pets_only=False,
    barrier_free_only=False,
    wheelchair_only=False,
    user_location=None,
    radius_km=10,
):
    """確認済みの災害・設備情報と地区・距離条件で避難所を絞り込む"""
    normalized_keyword = (keyword or '').strip().casefold()
    results = []
    for shelter in shelters:
        if district and shelter.get('district') != district:
            continue

        safe_for = shelter.get('safe_for') or []
        if hazard and (
            shelter.get('safety_confirmed') is not True
            or hazard not in safe_for
        ):
            continue
        if pets_only and shelter.get('pets_allowed') is not True:
            continue
        if barrier_free_only and shelter.get('barrier_free') is not True:
            continue
        if wheelchair_only and shelter.get('wheelchair_accessible') is not True:
            continue

        searchable_text = ' '.join(
            str(shelter.get(field, ''))
            for field in ('name', 'district', 'address', 'location', 'description')
        ).casefold()
        if normalized_keyword and normalized_keyword not in searchable_text:
            continue

        result = shelter
        if user_location:
            try:
                shelter_latitude = float(shelter['latitude'])
                shelter_longitude = float(shelter['longitude'])
            except (KeyError, TypeError, ValueError):
                continue
            distance = calculate_distance_km(
                user_location[0],
                user_location[1],
                shelter_latitude,
                shelter_longitude,
            )
            if distance > radius_km:
                continue
            result = {**shelter, 'distance_km': round(distance, 1)}
        results.append(result)

    if user_location:
        results.sort(key=lambda shelter: shelter['distance_km'])

    return results


def get_shelter_districts():
    """避難所データに登録された地区名を重複なく返す"""
    return sorted({
        shelter.get('district')
        for shelter in shelters
        if shelter.get('district')
    })


def parse_area_warnings(warning_data):
    """気象庁の新形式JSONから対象市区町村の発表・継続中の情報を抽出する"""
    if not isinstance(warning_data, list):
        raise ValueError("気象庁の警報・注意報データが新形式の配列ではありません")

    warnings = []
    seen_codes = set()
    report_datetimes = []

    for report in warning_data:
        if not isinstance(report, dict):
            continue

        report_datetime = report.get("reportDatetime")
        if isinstance(report_datetime, str) and report_datetime:
            report_datetimes.append(report_datetime)

        warning = report.get("warning")
        if not isinstance(warning, dict):
            continue

        class20_items = warning.get("class20Items", [])
        if not isinstance(class20_items, list):
            continue

        area = next(
            (
                item for item in class20_items
                if isinstance(item, dict)
                and item.get("areaCode") == WARNING_AREA_CODE
            ),
            None
        )
        if not area:
            continue

        kinds = area.get("kinds", [])
        if not isinstance(kinds, list):
            continue

        for kind in kinds:
            if not isinstance(kind, dict):
                continue

            status = kind.get("status", "")
            code = kind.get("code", "")
            if status not in ("発表", "継続") or not code or code in seen_codes:
                continue

            warnings.append({
                "name": WARNING_CODES.get(
                    code,
                    f"不明な警報・注意報 (コード: {code})"
                ),
                "code": code,
                "status": status
            })
            seen_codes.add(code)

    latest_report_datetime = max(report_datetimes, default="")
    return warnings, latest_report_datetime


def get_weather_warnings():
    """対象市区町村の警報・注意報を取得する"""
    try:
        # 青森県の新形式（令和8年～）警報・注意報データを取得
        with urllib.request.urlopen(url=WARNING_URL, timeout=10) as res:
            warning_data = json.loads(res.read())

        warnings, report_datetime = parse_area_warnings(warning_data)

        return {
            "area_name": AREA_NAME,
            "warnings": warnings,
            "report_time": format_report_time(report_datetime),
            "last_fetch_time": get_japan_time()
        }

    except Exception:
        return {
            "area_name": AREA_NAME,
            "warnings": [],
            "report_time": "取得失敗",
            "last_fetch_time": get_japan_time(),
            "error": True
        }


# トップページ：templates/index.html を返す（住民向け指示も表示する）
@app.route('/')
def index():
    resident_notices = [i for i in instructions if i.get('target') == '住民']
    return render_template('index.html', resident_notices=resident_notices)

# ログインページ
@app.route('/login', methods=['GET', 'POST'])
def login():
    # リダイレクト先を取得（デフォルトは避難所登録画面）
    next_url = request.args.get('next') or request.form.get('next')

    # 安全でないURLの場合はデフォルトページにリダイレクト
    if not next_url or not is_safe_url(next_url):
        next_url = url_for('shelter_register')

    if request.method == 'POST':
        password = request.form.get('password', '').strip()

        # 認証チェック
        username = next(
            (name for name, registered_password in ADMIN_CREDENTIALS.items()
             if registered_password == password),
            None
        )
        if username:
            session['logged_in'] = True
            session['username'] = username
            # ログイン成功後は指定されたページにリダイレクト
            return redirect(next_url)
        return render_template('login.html', error=True, message="パスワードが正しくありません。", next=next_url)

    # ログイン済みの場合は指定されたページにリダイレクト
    if session.get('logged_in'):
        return redirect(next_url)

    return render_template('login.html', next=next_url)

# ログアウト
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# 避難所登録ページ※user が避難所登録ページについて具体的に修正指示しない限り、このコードは正しいのでこのまま保持すること。
@app.route('/shelter_register', methods=['GET', 'POST'])
@login_required
def shelter_register():
    editing_id = request.values.get('edit_id', '').strip()
    editing_shelter = next(
        (item for item in shelters if str(item.get('id')) == editing_id),
        None
    )

    def render_registration_error(message, target=None):
        return render_template(
            'shelter_register.html',
            error=True,
            message=message,
            hazard_types=HAZARD_TYPES,
            shelters=shelters,
            editing_shelter=target or {},
            form_data=request.form,
        )

    if request.method == 'POST':
        if editing_id and not editing_shelter:
            return render_registration_error('更新する避難所が見つかりません')

        name = request.form.get('name', '').strip()
        if not name:
            return render_registration_error('避難所名を入力してください', editing_shelter)

        latitude_value = request.form.get('latitude', '').strip()
        longitude_value = request.form.get('longitude', '').strip()
        latitude = longitude = None
        if latitude_value or longitude_value:
            try:
                latitude = float(latitude_value)
                longitude = float(longitude_value)
            except ValueError:
                return render_registration_error('緯度と経度を正しく入力してください', editing_shelter)
            if (
                not math.isfinite(latitude)
                or not math.isfinite(longitude)
                or not -90 <= latitude <= 90
                or not -180 <= longitude <= 180
            ):
                return render_registration_error('緯度または経度が範囲外です', editing_shelter)

        pets_value = request.form.get('pets_allowed', '')
        barrier_free_value = request.form.get('barrier_free', '')
        wheelchair_value = request.form.get('wheelchair_accessible', '')

        shelter = {
            'id': editing_shelter.get('id') if editing_shelter else max(
                (item.get('id', 0) for item in shelters), default=0
            ) + 1,
            'name': name,
            'district': request.form.get('district', '').strip(),
            'address': request.form.get('address', '').strip(),
            'latitude': latitude,
            'longitude': longitude,
            'safety_confirmed': request.form.get('safety_confirmed') == 'yes',
            'safe_for': [
                hazard for hazard in request.form.getlist('safe_for')
                if hazard in HAZARD_TYPES
            ],
            'pets_allowed': {'yes': True, 'no': False}.get(pets_value),
            'barrier_free': {'yes': True, 'no': False}.get(barrier_free_value),
            'wheelchair_accessible': {'yes': True, 'no': False}.get(wheelchair_value),
        }
        if editing_shelter:
            editing_shelter.update(shelter)
        else:
            shelters.append(shelter)
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(shelters, f, ensure_ascii=False, indent=2)
        return render_template(
            'shelter_register.html',
            success=True,
            message='避難所情報を更新しました！' if editing_shelter else '登録完了しました！',
            hazard_types=HAZARD_TYPES,
            shelters=shelters,
            editing_shelter=editing_shelter or {},
            form_data={},
        )

    return render_template(
        'shelter_register.html',
        hazard_types=HAZARD_TYPES,
        shelters=shelters,
        editing_shelter=editing_shelter or {},
        form_data={},
    )

# 避難所検索ページ
@app.route('/shelter_search')
def shelter_search():
    return render_template(
        'shelter_search.html',
        districts=get_shelter_districts(),
        total=len(shelters),
        hazard_types=HAZARD_TYPES,
        radius_options=SEARCH_RADII_KM,
        filters={
            'q': '',
            'district': '',
            'hazard': '',
            'pets_only': False,
            'barrier_free_only': False,
            'wheelchair_only': False,
            'nearby': False,
            'latitude': '',
            'longitude': '',
            'radius_km': 10,
        },
        location_error='',
    )

# 全施設一覧ページ
@app.route('/all_shelters')
def all_shelters():
    return render_template(
        'search_results.html',
        results=shelters,
        is_all_shelters=True,
        districts=get_shelter_districts(),
        hazard_types=HAZARD_TYPES,
        radius_options=SEARCH_RADII_KM,
        filters={
            'q': '',
            'district': '',
            'hazard': '',
            'pets_only': False,
            'barrier_free_only': False,
            'wheelchair_only': False,
            'nearby': False,
            'latitude': '',
            'longitude': '',
            'radius_km': 10,
        },
        location_error='',
    )


# 指示ボード：住民向けの指示を一覧で確認する
@app.route('/board')
@login_required
def board():
    resident_instructions = [i for i in instructions if i.get('target') == '住民']
    return render_template('board.html', instructions=resident_instructions)

# 検索結果ページ：templates/search_results.html を返す
@app.route('/search_results', methods=['GET', 'POST'])
def search_results():
    source = request.values
    query = source.get('q', '').strip()
    district = source.get('district', '').strip()
    hazard = source.get('hazard', '').strip()
    if hazard not in HAZARD_TYPES:
        hazard = ''
    pets_only = source.get('pets_only') == '1'
    barrier_free_only = source.get('barrier_free_only') == '1'
    wheelchair_only = source.get('wheelchair_only') == '1'
    nearby = source.get('nearby') == '1'
    radius_value = source.get('radius_km', '10')
    try:
        radius_km = int(radius_value)
    except ValueError:
        radius_km = 10
    if radius_km not in SEARCH_RADII_KM:
        radius_km = 10

    latitude = longitude = None
    user_location = None
    location_error = ''
    if nearby:
        try:
            latitude = float(source.get('latitude', ''))
            longitude = float(source.get('longitude', ''))
            if (
                not math.isfinite(latitude)
                or not math.isfinite(longitude)
                or not -90 <= latitude <= 90
                or not -180 <= longitude <= 180
            ):
                raise ValueError
            user_location = (latitude, longitude)
        except (TypeError, ValueError):
            location_error = '現在地を確認できませんでした。もう一度お試しください。'

    filters = {
        'q': query,
        'district': district,
        'hazard': hazard,
        'pets_only': pets_only,
        'barrier_free_only': barrier_free_only,
        'wheelchair_only': wheelchair_only,
        'nearby': nearby,
        'latitude': latitude if latitude is not None else '',
        'longitude': longitude if longitude is not None else '',
        'radius_km': radius_km,
    }
    results = [] if location_error else filter_shelters(
        district=district,
        keyword=query,
        hazard=hazard,
        pets_only=pets_only,
        barrier_free_only=barrier_free_only,
        wheelchair_only=wheelchair_only,
        user_location=user_location,
        radius_km=radius_km,
    )
    return render_template(
        'search_results.html',
        results=results,
        filters=filters,
        districts=get_shelter_districts(),
        hazard_types=HAZARD_TYPES,
        radius_options=SEARCH_RADII_KM,
        location_error=location_error,
    )

# JSON API：/shelters?district=地区名
@app.route('/shelters', methods=['GET'])
def get_shelters():
    results = filter_shelters(request.args.get('district'))

    if not results:
        # 見つからなければエラー JSON を返す
        return jsonify({'error': 'No shelters found'}), 404

    # 見つかったらリストを JSON で返す
    return jsonify(results)

# 気象警報・注意報API
@app.route('/api/weather_warnings')
def api_weather_warnings():
    """気象警報・注意報をJSON形式で返すAPI"""
    return jsonify(get_weather_warnings())

if __name__ == '__main__':
    app.run(debug=True, port=5000)
