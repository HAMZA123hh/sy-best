import os
import requests
from datetime import datetime, timedelta
from flask import Flask, render_template, jsonify, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)

# إعداد مفتاح السيرفر السري (تثبيت نص قوي لمنع ضياع الجلسات بعد إعادة التشغيل)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'SyBest_Super_Secret_Key_2026_HM_12345')

# ضبط مسار قاعدة البيانات تلقائياً وبشكل مطلق لمنع أخطاء التشغيل المحلي (OperationalError)
database_uri = os.environ.get('DATABASE_URL')
if database_uri:
    if database_uri.startswith("postgres://"):
        database_uri = database_uri.replace("postgres://", "postgresql://", 1)
else:
    # تحديد مسار المجلد الحالي للمشروع بدقة وإرفاق مجلد instance ديناميكياً
    BASE_DIR = os.path.abspath(os.path.dirname(__file__))
    INSTANCE_DIR = os.path.join(BASE_DIR, 'instance')
    
    # إنشاء مجلد instance تلقائياً لو كان غير موجود لمنع خطأ الـ SQLite
    if not os.path.exists(INSTANCE_DIR):
        os.makedirs(INSTANCE_DIR)
        
    database_uri = f"sqlite:///{os.path.join(INSTANCE_DIR, 'database.db')}"

app.config['SQLALCHEMY_DATABASE_URI'] = database_uri
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# جلب مفتاح الـ API الخاص بـ TMDB من البيئة أو استخدام الافتراضي
API_KEY = os.environ.get('TMDB_API_KEY', '6077e25ec7e3d48f522d81ed13e9b938')

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# ---------- نماذج قاعدة البيانات المطورة ----------
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    subscription = db.Column(db.String(20), default='vip')  # يبدأ بـ vip للاستفادة من الفترة التجريبية
    expire_date = db.Column(db.DateTime, nullable=True)     # سيتم تعيينه لـ 24 ساعة عند التسجيل
    is_admin = db.Column(db.Boolean, default=False)

class PaymentRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    method = db.Column(db.String(50), nullable=False)
    transaction_id = db.Column(db.String(100), unique=True, nullable=False)
    plan_type = db.Column(db.String(20), default='monthly') # باقة (monthly أو yearly)
    status = db.Column(db.String(20), default='pending')     # pending, approved, rejected
    username = db.Column(db.String(150))
    date_created = db.Column(db.DateTime, default=datetime.utcnow)

class Feedback(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    username = db.Column(db.String(150))
    message_type = db.Column(db.String(50))                # "طلب محتوى" أو "اقتراح/مشكلة"
    content = db.Column(db.Text, nullable=False)
    date_created = db.Column(db.DateTime, default=datetime.utcnow)

@login_manager.user_loader
def load_user(user_id):
    user = User.query.get(int(user_id))
    # فحص انتهاء الاشتراك فقط للمستخدمين العاديين وليس للأدمن
    if user and not user.is_admin and user.subscription == 'vip' and user.expire_date:
        if datetime.utcnow() > user.expire_date:
            user.subscription = 'free'
            user.expire_date = None
            db.session.commit()
    return user

# معالج سياق تلقائي لتمرير حالة وحساب ساعات وأيام الـ VIP لجميع القوالب دون استثناء
@app.context_processor
def inject_vip_status():
    vip_status = 'free'
    time_details = {"days": 0, "hours": 0, "minutes": 0}
    
    if current_user and current_user.is_authenticated:
        if current_user.is_admin:
            vip_status = 'admin'
        elif current_user.subscription == 'vip' and current_user.expire_date:
            now = datetime.utcnow()
            if now < current_user.expire_date:
                time_left = current_user.expire_date - now
                total_seconds = time_left.total_seconds()
                
                # حساب الأيام والساعات والدقائق بدقة تفصيلية
                days = int(total_seconds // 86400)
                hours = int((total_seconds % 86400) // 3600)
                minutes = int((total_seconds % 3600) // 60)
                
                time_details = {"days": days, "hours": hours, "minutes": minutes}
                
                # إذا كان الاشتراك الإجمالي المتبقي أقل من أو يساوي 24 ساعة نعتبرها تجريبية
                if (total_seconds / 3600) <= 24:
                    vip_status = 'trial'
                else:
                    vip_status = 'active'
            else:
                vip_status = 'free'
        elif current_user.subscription == 'pending':
            vip_status = 'pending'
            
    return dict(vip_status=vip_status, vip_time_left=time_details)

# إنشاء قاعدة البيانات والمجلدات تلقائياً في حال عدم وجودها
with app.app_context():
    db.create_all()

# دالة مساعدة مضافة لتحويل أسماء الأفلام والمسلسلات الأجنبية إلى الإنجليزية تلقائياً
def fix_titles(data):
    if isinstance(data, dict) and 'results' in data:
        for item in data['results']:
            # إذا لم تكن اللغة الأصلية عربية ولا تركية، استخدم الاسم الإنجليزي الأصلي
            if item.get('original_language') not in ['ar', 'tr']:
                if 'title' in item and item.get('original_title'):
                    item['title'] = item['original_title']
                if 'name' in item and item.get('original_name'):
                    item['name'] = item['original_name']
    return data

# ---------- الراوتات الأساسية ----------
@app.route('/')
def home():
    return render_template('index.html')

@app.route('/about')
def about():
    return render_template('about.html')

@app.route('/feedback', methods=['GET', 'POST'])
@login_required
def feedback():
    if request.method == 'POST':
        message_type = request.form.get('message_type')
        content = request.form.get('content', '').strip()
        if not content:
            flash('الرجاء كتابة تفاصيل الطلب أو الرأي!', 'error')
            return redirect(url_for('feedback'))
        
        new_feedback = Feedback(
            user_id=current_user.id,
            username=current_user.username,
            message_type=message_type,
            content=content
        )
        db.session.add(new_feedback)
        db.session.commit()
        flash('تم إرسال طلبك بنجاح! شكراً لمساهمتك في تطوير Sy-Best.', 'success')
        return redirect(url_for('home'))
    return render_template('feedback.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('home'))
        
    if request.method == 'POST':
        login_input = request.form.get('email', '').strip()
        if not login_input:
            login_input = request.form.get('username', '').strip()
            
        password = request.form.get('password', '')
        remember = True if request.form.get('remember') else False
        
        user = User.query.filter((User.email == login_input) | (User.username == login_input)).first()
        
        if user and check_password_hash(user.password, password):
            login_user(user, remember=remember)
            flash(f'مرحباً بعودتك، {user.username}! تم تسجيل الدخول بنجاح.', 'success')
            return redirect(url_for('home'))
        else:
            flash('البريد الإلكتروني أو كلمة المرور غير صحيحة، حاول مجدداً.', 'error')
            return redirect(url_for('login'))
            
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('home'))
        
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        
        if User.query.filter_by(username=username).first():
            flash('اسم المستخدم مسجل بالفعل! اختر اسماً آخر.', 'error')
            return redirect(url_for('register'))
            
        if User.query.filter_by(email=email).first():
            flash('البريد الإلكتروني مستخدم سابقاً! حاول تسجيل الدخول.', 'error')
            return redirect(url_for('register'))
            
        hashed_password = generate_password_hash(password)
        is_first_user = (User.query.count() == 0)
        
        if is_first_user:
            new_user = User(
                username=username,
                email=email,
                password=hashed_password,
                subscription='vip',
                expire_date=None,
                is_admin=True
            )
        else:
            new_user = User(
                username=username,
                email=email,
                password=hashed_password,
                subscription='vip',
                expire_date=datetime.utcnow() + timedelta(hours=24),
                is_admin=False
            )
            
        db.session.add(new_user)
        db.session.commit()
        
        if is_first_user:
            flash('تم إنشاء حسابك بنجاح كمدير أول للموقع بالكامل! 👑', 'success')
        else:
            flash('تهانينا! تم إنشاء حسابك بنجاح وبدأت فترتك التجريبية الـ VIP المجانية (24 ساعة). 🎉', 'success')
            
        return redirect(url_for('login'))
        
    return render_template('register.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('تم تسجيل الخروج بنجاح. نتمنى رؤيتك قريباً!', 'success')
    return redirect(url_for('home'))

@app.route('/pay', methods=['GET', 'POST'])
@login_required
def pay():
    has_pending = PaymentRequest.query.filter_by(user_id=current_user.id, status='pending').first()
    
    if request.method == 'POST':
        method = request.form.get('method')
        plan_type = request.form.get('plan_type', 'monthly')
        transaction_id = request.form.get('transaction_id', '').strip()
        
        if not method or not transaction_id:
            flash('جميع الحقول مطلوبة لإرسال طلب التفعيل!', 'error')
            return redirect(url_for('pay'))
            
        if PaymentRequest.query.filter_by(transaction_id=transaction_id).first():
            flash('رقم العملية هذا تم إرساله مسبقاً وقيد المراجعة!', 'error')
            return redirect(url_for('pay'))
            
        new_payment = PaymentRequest(
            user_id=current_user.id,
            method=method,
            plan_type=plan_type,
            transaction_id=transaction_id,
            username=current_user.username
        )
        current_user.subscription = 'pending'
        db.session.add(new_payment)
        db.session.commit()
        flash('تم إرسال طلبك بنجاح! سيتم تفعيل حسابك بعد مراجعة الحوالة.', 'success')
        return redirect(url_for('home'))
        
    return render_template('pay.html', has_pending=has_pending)

# ---------- راوتات الـ API المطورة والمعدلة بالكامل ----------
@app.route('/api/movies')
def get_movies():
    page = request.args.get('page', 1, type=int)
    genre = request.args.get('genre', '', type=str)
    query = request.args.get('query', '', type=str)
    
    if query:
        url = f"https://api.themoviedb.org/3/search/movie?api_key={API_KEY}&query={query}&language=ar-SA&page={page}"
    elif genre:
        url = f"https://api.themoviedb.org/3/discover/movie?api_key={API_KEY}&language=ar-SA&sort_by=popularity.desc&page={page}&with_genres={genre}"
    else:
        url = f"https://api.themoviedb.org/3/movie/popular?api_key={API_KEY}&language=ar-SA&page={page}"
        
    try:
        response = requests.get(url, timeout=10)
        # تم تطبيق دالة تصحيح العناوين هنا تلقائياً
        return jsonify(fix_titles(response.json()))
    except Exception as e:
        return jsonify({"results": [], "error": str(e)})

@app.route('/api/shows')
def get_shows():
    page = request.args.get('page', 1, type=int)
    genre = request.args.get('genre', '', type=str)
    query = request.args.get('query', '', type=str)
    
    if query:
        url = f"https://api.themoviedb.org/3/search/tv?api_key={API_KEY}&query={query}&language=ar-SA&page={page}"
    elif genre:
        url = f"https://api.themoviedb.org/3/discover/tv?api_key={API_KEY}&language=ar-SA&sort_by=popularity.desc&page={page}&with_genres={genre}"
    else:
        url = f"https://api.themoviedb.org/3/tv/popular?api_key={API_KEY}&language=ar-SA&page={page}"
        
    try:
        response = requests.get(url, timeout=10)
        # تم تطبيق دالة تصحيح العناوين هنا تلقائياً
        return jsonify(fix_titles(response.json()))
    except Exception as e:
        return jsonify({"results": [], "error": str(e)})

@app.route('/api/arabic-movies')
def get_arabic_movies():
    page = request.args.get('page', 1, type=int)
    url = f"https://api.themoviedb.org/3/discover/movie?api_key={API_KEY}&language=ar-SA&sort_by=primary_release_date.desc&page={page}&with_original_language=ar"
    try:
        response = requests.get(url, timeout=10)
        return jsonify(response.json())
    except Exception as e:
        return jsonify({"results": []})

@app.route('/api/arabic-shows')
def get_arabic_shows():
    page = request.args.get('page', 1, type=int)
    url = f"https://api.themoviedb.org/3/discover/tv?api_key={API_KEY}&language=ar-SA&sort_by=popularity.desc&page={page}&with_original_language=ar"
    try:
        response = requests.get(url, timeout=10)
        return jsonify(response.json())
    except Exception as e:
        return jsonify({"results": []})

@app.route('/api/turkish-shows')
def get_turkish_shows():
    page = request.args.get('page', 1, type=int)
    url = f"https://api.themoviedb.org/3/discover/tv?api_key={API_KEY}&language=ar-SA&sort_by=popularity.desc&page={page}&with_original_language=tr"
    try:
        response = requests.get(url, timeout=10)
        return jsonify(response.json())
    except Exception as e:
        return jsonify({"results": [], "error": str(e)})

@app.route('/api/search')
def global_search():
    query = request.args.get('query', '', type=str)
    media_type = request.args.get('type', 'movie', type=str)
    page = request.args.get('page', 1, type=int)
    
    if not query:
        return jsonify({"results": []})
        
    endpoint = "search/movie" if media_type == "movie" else "search/tv"
    url = f"https://api.themoviedb.org/3/{endpoint}?api_key={API_KEY}&query={query}&language=ar-SA&page={page}"
    
    try:
        response = requests.get(url, timeout=10)
        # تم تطبيق دالة تصحيح العناوين هنا تلقائياً لتعمل مع نتائج البحث
        return jsonify(fix_titles(response.json()))
    except Exception as e:
        return jsonify({"results": [], "error": str(e)})

@app.route('/api/movie-video/<int:movie_id>')
def get_movie_video(movie_id):
    url = f"https://api.themoviedb.org/3/movie/{movie_id}/videos?api_key={API_KEY}"
    try:
        response = requests.get(url, timeout=10)
        return jsonify(response.json())
    except Exception as e:
        return jsonify({"results": [], "error": str(e)})

@app.route('/api/show-video/<int:tv_id>')
def get_show_video(tv_id):
    url = f"https://api.themoviedb.org/3/tv/{tv_id}/videos?api_key={API_KEY}"
    try:
        response = requests.get(url, timeout=10)
        return jsonify(response.json())
    except Exception as e:
        return jsonify({"results": [], "error": str(e)})

@app.route('/movie/<int:movie_id>')
def view_movie(movie_id):
    url = f"https://api.themoviedb.org/3/movie/{movie_id}?api_key={API_KEY}&language=ar-SA"
    try:
        res = requests.get(url, timeout=10)
        media_data = res.json() if res.status_code == 200 else None
    except:
        media_data = None
        
    if not media_data:
        flash("عذراً، لم نتمكن من جلب بيانات الفيلم المطلوبة من السيرفر.", "error")
        return redirect(url_for('home'))
        
    # تصحيح العنوان لصفحة عرض الفيلم الفردية إذا كان العمل أجنبياً
    if media_data.get('original_language') not in ['ar', 'tr']:
        if 'title' in media_data and media_data.get('original_title'):
            media_data['title'] = media_data['original_title']
        
    return render_template('movie.html', media=media_data, is_tv=False, media_id=movie_id)

@app.route('/tv/<int:tv_id>')
@app.route('/tv/<int:tv_id>/<int:season>/<int:episode>')
def view_tv(tv_id, season=1, episode=1):
    url = f"https://api.themoviedb.org/3/tv/{tv_id}?api_key={API_KEY}&language=ar-SA"
    try:
        res = requests.get(url, timeout=10)
        media_data = res.json() if res.status_code == 200 else None
    except:
        media_data = None
        
    if not media_data:
        flash("عذراً، لم نتمكن من جلب تفاصيل المسلسل.", "error")
        return redirect(url_for('home'))
        
    # تصحيح العنوان لصفحة عرض المسلسل الفردية إذا كان العمل أجنبياً
    if media_data.get('original_language') not in ['ar', 'tr']:
        if 'name' in media_data and media_data.get('original_name'):
            media_data['name'] = media_data['original_name']
        
    seasons_count = media_data.get('number_of_seasons', 1)
    total_seasons = list(range(1, seasons_count + 1))
    
    season_url = f"https://api.themoviedb.org/3/tv/{tv_id}/season/{season}?api_key={API_KEY}&language=ar-SA"
    try:
        s_response = requests.get(season_url, timeout=15)
        season_data = s_response.json() if s_response.status_code == 200 else {}
    except:
        season_data = {}
        
    episodes = season_data.get('episodes', [])
    if not episodes:
        episodes_list = list(range(1, 21))
    else:
        episodes_list = [ep.get('episode_number') for ep in episodes]
        
    return render_template('movie.html', 
                           media=media_data, 
                           is_tv=True, 
                           media_id=tv_id,
                           current_season=season,
                           current_episode=episode,
                           total_seasons=total_seasons,
                           episodes_detailed=episodes)

# ---------- لوحة التحكم والعمليات الإدارية المحمية بشكل صارم ----------
@app.route('/admin', methods=['GET', 'POST'])
@login_required
def admin():
    if not current_user.is_admin:
        return "🔒 غير مسموح لك بالدخول! هذه المنطقة محمية لمدير الموقع فقط.", 403
        
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'approve_payment':
            pay_id = request.form.get('pay_id')
            payment = PaymentRequest.query.get(pay_id)
            if payment:
                payment.status = 'approved'
                user = User.query.get(payment.user_id)
                if user:
                    user.subscription = 'vip'
                    days_to_add = 365 if payment.plan_type == 'yearly' else 30
                    user.expire_date = datetime.utcnow() + timedelta(days=days_to_add)
                db.session.commit()
                flash('تمت الموافقة على الدفعة وتفعيل الـ VIP للمستخدم بنجاح.', 'success')
                
        elif action == 'reject_payment':
            pay_id = request.form.get('pay_id')
            payment = PaymentRequest.query.get(pay_id)
            if payment:
                payment.status = 'rejected'
                user = User.query.get(payment.user_id)
                if user and user.subscription == 'pending':
                    user.subscription = 'free'
                db.session.commit()
                flash('تم رفض طلب التحويل وإرجاع الحساب كحساب مجاني.', 'warning')
                
        elif action == 'delete_feedback':
            feed_id = request.form.get('feed_id')
            feed_item = Feedback.query.get(feed_id)
            if feed_item:
                db.session.delete(feed_item)
                db.session.commit()
                flash('تم مسح الرسالة بنجاح.', 'success')
            
    try:
        pending_payments = PaymentRequest.query.filter_by(status='pending').all() or []
        all_users = User.query.all() or []
        all_feedbacks = Feedback.query.order_by(Feedback.date_created.desc()).all() or []
    except Exception:
        pending_payments = []
        all_users = []
        all_feedbacks = []
    
    return render_template('admin.html', payments=pending_payments, users=all_users, feedbacks=all_feedbacks)

@app.route('/secret-reset/<string:username>/<string:new_password>')
@login_required
def secret_reset(username, new_password):
    if not current_user.is_admin:
        return "🔒 غير مسموح لك بالدخول!", 403
        
    user = User.query.filter_by(username=username).first()
    if user:
        user.password = generate_password_hash(new_password)
        db.session.commit()
        return f"تم تغيير باسوورد {username} بنجاح إلى الباسوورد الجديد.", 200
    return "المستخدم غير موجود", 404

if __name__ == '__main__':
    app.run(debug=True, port=5000)