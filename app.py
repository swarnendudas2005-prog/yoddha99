from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from datetime import datetime
from deep_translator import GoogleTranslator
from twilio.rest import Client
from werkzeug.utils import secure_filename
import pandas as pd
import os
import random

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secretkey123'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///farm.db'

# --- IMAGE UPLOAD CONFIG ---
UPLOAD_FOLDER = 'static/product_images'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# --- TWILIO CONFIG ---
app.config['TWILIO_ACCOUNT_SID'] = 'ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxx' 
app.config['TWILIO_AUTH_TOKEN'] = 'your_auth_token_here'
app.config['TWILIO_PHONE_NUMBER'] = '+15550000000'

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# --- MODELS ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    phone = db.Column(db.String(20), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)
    role = db.Column(db.String(50), nullable=False)

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    price = db.Column(db.Float, nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    category = db.Column(db.String(50))
    location = db.Column(db.String(150), nullable=True, default='Not specified')
    image = db.Column(db.String(150), nullable=True, default='default.jpg')
    farmer_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    farmer = db.relationship('User', foreign_keys=[farmer_id])

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'))
    consumer_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    farmer_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    quantity = db.Column(db.Integer, nullable=False, default=1) 
    status = db.Column(db.String(50), default='Pending')
    date = db.Column(db.DateTime, default=datetime.utcnow)
    
    product = db.relationship('Product', backref='orders')
    consumer = db.relationship('User', foreign_keys=[consumer_id], backref='my_orders')
    farmer = db.relationship('User', foreign_keys=[farmer_id], backref='sales', overlaps="farmer")

class ActivityLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    action = db.Column(db.String(200), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship('User', backref='activities')

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- FORECASTING ---
class DemandForecaster:
    def __init__(self, data_file='historical_data.csv'):
        if os.path.exists(data_file):
            self.df = pd.read_csv(data_file)
            try: self.df['date'] = pd.to_datetime(self.df['date'])
            except: pass 
        else:
            self.df = pd.DataFrame()

    def analyze(self, product_name):
        if self.df.empty: return "No Historical Data", 0, 0
        product_data = self.df[self.df['product_name'].str.lower() == product_name.lower()]
        if product_data.empty: return "New Product", 0, 0
        
        current_month = datetime.now().month
        if 'date' in product_data.columns:
            seasonal_data = product_data[product_data['date'].dt.month == current_month]
        else: seasonal_data = pd.DataFrame()
        
        if not seasonal_data.empty:
            avg_price = seasonal_data['price_per_kg'].mean()
            avg_qty = seasonal_data['quantity_sold'].mean()
            overall_avg_qty = product_data['quantity_sold'].mean()
            if avg_qty > overall_avg_qty * 1.1: trend = "High Demand (Seasonal Peak) 📈"
            elif avg_qty < overall_avg_qty * 0.9: trend = "Low Demand (Off-Season) 📉"
            else: trend = "Stable Demand ⚖️"
        else:
            avg_price = product_data['price_per_kg'].mean()
            avg_qty = product_data['quantity_sold'].mean()
            trend = "Stable (No seasonal data)"
        return trend, round(avg_price, 2), int(avg_qty)

forecaster = DemandForecaster()

# --- TRANSLATION ---
@app.context_processor
def inject_translator():
    def translate_text(text):
        try:
            dest_lang = session.get('lang', 'en')
            if dest_lang == 'en': return text
            return GoogleTranslator(source='auto', target=dest_lang).translate(text)
        except Exception as e:
            print(f"Translation Error: {e}")
            return text
    return dict(translate=translate_text)

@app.route('/set_language/<lang_code>')
def set_language(lang_code):
    session['lang'] = lang_code
    return redirect(request.referrer or url_for('index'))

# --- ROUTES ---
@app.route('/')
def index(): return render_template('index.html')

@app.route('/about')
def about(): return render_template('about.html')

@app.route('/privacy')
def privacy(): return render_template('privacy.html')

@app.route('/customer_service')
def customer_service(): return render_template('customer_service.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        phone = request.form.get('phone')
        password = request.form.get('password')
        role = request.form.get('role')
        
        if User.query.filter((User.username == username) | (User.phone == phone)).first():
            flash('Username or Phone number already exists.')
            return redirect(url_for('register'))
            
        new_user = User(username=username, phone=phone, password=password, role=role)
        db.session.add(new_user)
        db.session.commit()
        
        db.session.add(ActivityLog(user_id=new_user.id, action=f'Registered as {role}'))
        db.session.commit()
        
        login_user(new_user)
        return redirect(url_for('dashboard'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        if user and user.password == password:
            login_user(user)
            
            db.session.add(ActivityLog(user_id=user.id, action='Logged in via password'))
            db.session.commit()
            
            return redirect(url_for('dashboard'))
        flash('Invalid credentials')
    return render_template('login.html')

@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        phone = request.form.get('phone')
        user = User.query.filter_by(phone=phone).first()
        if user:
            otp = random.randint(1000, 9999)
            session['otp'] = otp
            session['reset_user_id'] = user.id
            if 'ACxxxx' in app.config['TWILIO_ACCOUNT_SID']:
                flash(f"Test Mode (No Keys): Your OTP is {otp}")
                return redirect(url_for('verify_otp'))
            try:
                client = Client(app.config['TWILIO_ACCOUNT_SID'], app.config['TWILIO_AUTH_TOKEN'])
                message = client.messages.create(
                    body=f"Your Smart Farmer OTP is: {otp}",
                    from_=app.config['TWILIO_PHONE_NUMBER'],
                    to=phone
                )
                flash(f'OTP Sent via SMS to {phone}!')
                return redirect(url_for('verify_otp'))
            except Exception as e:
                flash(f"SMS Failed. Test Mode OTP: {otp}") 
                return redirect(url_for('verify_otp'))
        else:
            flash('Phone number not found.')
    return render_template('forgot_password.html')

@app.route('/verify_otp', methods=['GET', 'POST'])
def verify_otp():
    if request.method == 'POST':
        entered_otp = request.form.get('otp')
        saved_otp = session.get('otp')
        if saved_otp and int(entered_otp) == saved_otp:
            user_id = session.get('reset_user_id')
            user = User.query.get(user_id)
            login_user(user)
            session.pop('otp', None)
            
            db.session.add(ActivityLog(user_id=user.id, action='Logged in via OTP'))
            db.session.commit()
            
            flash('OTP Verified! Logged in successfully.')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid OTP. Please try again.')
    return render_template('verify_otp.html')

@app.route('/dashboard')
@login_required
def dashboard():
    if current_user.role == 'admin':
        return redirect(url_for('admin_dashboard'))
        
    elif current_user.role == 'farmer':
        my_products = Product.query.filter_by(farmer_id=current_user.id).all()
        incoming_orders = Order.query.filter_by(farmer_id=current_user.id).all()
        total_sales = Order.query.filter_by(farmer_id=current_user.id, status='Accepted').count()
        return render_template('farmer_dashboard.html', products=my_products, orders=incoming_orders, sales=total_sales)
    else:
        search_query = request.args.get('q', '')
        if search_query:
            products = Product.query.filter(
                (Product.name.ilike(f'%{search_query}%')) |
                (Product.category.ilike(f'%{search_query}%')) |
                (Product.location.ilike(f'%{search_query}%'))
            ).all()
        else:
            products = Product.query.all()
            
        my_orders = Order.query.filter_by(consumer_id=current_user.id).all()
        return render_template('consumer_dashboard.html', products=products, orders=my_orders, search_query=search_query)

@app.route('/admin_dashboard')
@login_required
def admin_dashboard():
    if current_user.role != 'admin':
        flash('Unauthorized Access!')
        return redirect(url_for('dashboard'))
        
    users = User.query.all()
    products = Product.query.all()
    orders = Order.query.order_by(Order.date.desc()).all()
    logs = ActivityLog.query.order_by(ActivityLog.timestamp.desc()).limit(100).all()
    
    total_sales_kg = sum([o.quantity for o in orders if o.status == 'Accepted'])
    total_revenue = sum([(o.quantity * o.product.price) for o in orders if o.status == 'Accepted'])
    total_inventory = sum([p.quantity for p in products])
    
    return render_template('admin_dashboard.html', 
                           users=users, 
                           products=products, 
                           orders=orders, 
                           logs=logs,
                           total_sales_kg=total_sales_kg,
                           total_revenue=total_revenue,
                           total_inventory=total_inventory)

@app.route('/check_forecast', methods=['POST'])
@login_required
def check_forecast():
    product_name = request.form.get('product_check')
    trend, avg_price, avg_qty = forecaster.analyze(product_name)
    my_products = Product.query.filter_by(farmer_id=current_user.id).all()
    incoming_orders = Order.query.filter_by(farmer_id=current_user.id).all()
    total_sales = Order.query.filter_by(farmer_id=current_user.id, status='Accepted').count()
    return render_template('farmer_dashboard.html', products=my_products, orders=incoming_orders, sales=total_sales, forecast_result={'name': product_name, 'trend': trend, 'price': avg_price, 'qty': avg_qty})

@app.route('/add_product', methods=['POST'])
@login_required
def add_product():
    if current_user.role != 'farmer': return redirect(url_for('index'))
    
    name = request.form.get('name')
    price = float(request.form.get('price'))
    qty = int(request.form.get('quantity'))
    category = request.form.get('category')
    location = request.form.get('location')
    
    image_file = request.files.get('image')
    filename = 'default.jpg'
    
    if image_file and allowed_file(image_file.filename):
        filename = secure_filename(image_file.filename)
        filename = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}"
        image_file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
    
    new_prod = Product(name=name, price=price, quantity=qty, category=category, location=location, image=filename, farmer_id=current_user.id)
    db.session.add(new_prod)
    db.session.commit()
    
    db.session.add(ActivityLog(user_id=current_user.id, action=f'Added new product: {name}'))
    db.session.commit()
    
    flash(f'Product Added!')
    return redirect(url_for('dashboard'))

@app.route('/buy/<int:product_id>', methods=['POST'])
@login_required
def buy_product(product_id):
    product = Product.query.get(product_id)
    order_qty = int(request.form.get('order_quantity', 1))
    
    if product and product.quantity >= order_qty and order_qty > 0:
        new_order = Order(
            product_id=product.id, 
            consumer_id=current_user.id, 
            farmer_id=product.farmer_id,
            quantity=order_qty
        )
        db.session.add(new_order)
        db.session.commit()
        
        db.session.add(ActivityLog(user_id=current_user.id, action=f'Placed order for {order_qty}kg of {product.name}'))
        db.session.commit()
        
        flash(f'Order placed for {order_qty} kg of {product.name}!')
    else:
        flash('Invalid quantity or out of stock!')
    return redirect(url_for('dashboard'))

@app.route('/manage_order/<int:order_id>/<action>')
@login_required
def manage_order(order_id, action):
    order = Order.query.get(order_id)
    if not order or order.farmer_id != current_user.id: return "Unauthorized"
    
    if action == 'accept':
        if order.product.quantity >= order.quantity:
            order.status = 'Accepted'
            order.product.quantity -= order.quantity
            
            db.session.add(ActivityLog(user_id=current_user.id, action=f'Accepted order #{order.id}'))
            flash('Order Accepted!')
        else:
            flash("Not enough stock left to accept this order!")
    elif action == 'reject': 
        order.status = 'Rejected'
        db.session.add(ActivityLog(user_id=current_user.id, action=f'Rejected order #{order.id}'))
        flash('Order Rejected.')
    
    db.session.commit()
    return redirect(url_for('dashboard'))

@app.route('/logout')
@login_required
def logout():
    db.session.add(ActivityLog(user_id=current_user.id, action='Logged out'))
    db.session.commit()
    
    logout_user()
    return redirect(url_for('index'))

if __name__ == '__main__':
    with app.app_context(): 
        # Create all tables securely
        db.create_all()
        
        # --- HARDCODED ADMIN ACCOUNT CREATION ---
        admin_user = User.query.filter_by(username='Subhajit Rudra').first()
        if not admin_user:
            new_admin = User(
                username='Subhajit Rudra', 
                phone='Admin', 
                password='Subhajit2005', 
                role='admin'
            )
            db.session.add(new_admin)
            db.session.commit()
            print("Master Admin Account 'Subhajit Rudra' successfully generated.")
            
    # --- FIXED: Use generic host and NO debug mode for Streamlit Cloud compatibility ---
    # This prevents the "signal only works in main thread" error.
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
