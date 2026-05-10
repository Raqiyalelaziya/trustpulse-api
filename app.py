from flask import Flask, request, jsonify
from flask_cors import CORS
import mysql.connector
import bcrypt
import jwt
import datetime
import os

app = Flask(__name__)

# CORS - Allow ALL origins
CORS(app, resources={r"/*": {"origins": "*"}})

@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    return response

SECRET_KEY = os.environ.get("SECRET_KEY", "trustpulse-secret-2024")

def get_db():
    return mysql.connector.connect(
        host=os.environ.get("DB_HOST", "gondola.proxy.rlwy.net"),
        port=int(os.environ.get("DB_PORT", 37338)),
        user=os.environ.get("DB_USER", "root"),
        password=os.environ.get("DB_PASSWORD", "rCGgmsQSBGOncYmGnpNdhSHirCkOktNm"),
        database=os.environ.get("DB_NAME", "railway")
    )

def decode_token(request):
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not token:
        return None
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
    except:
        return None

@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "TrustPulse API running", "version": "2.0"})

@app.route("/auth/signup", methods=["POST", "OPTIONS"])
def signup():
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"}), 200
    
    try:
        data = request.json
        email = data.get("email", "").strip().lower()
        password = data.get("password", "")
        full_name = data.get("full_name", "")

        if not email or not password:
            return jsonify({"error": "Email and password required"}), 400

        hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        db = get_db()
        cursor = db.cursor(dictionary=True)

        try:
            cursor.execute(
                "INSERT INTO users (email, password_hash, full_name, role, profile_completeness, trust_score) VALUES (%s, %s, %s, 'user', 0, 0)",
                (email, hashed, full_name)
            )
            db.commit()
            user_id = cursor.lastrowid
            
            # Try to add reward points, but don't fail if table doesn't exist
            try:
                cursor.execute("INSERT INTO reward_points (user_id, points_balance) VALUES (%s, 0)", (user_id,))
                db.commit()
            except:
                pass

            token = jwt.encode({
                "user_id": user_id,
                "email": email,
                "exp": datetime.datetime.utcnow() + datetime.timedelta(days=30)
            }, SECRET_KEY, algorithm="HS256")

            return jsonify({
                "token": token, 
                "user_id": user_id, 
                "email": email, 
                "full_name": full_name
            }), 201
            
        except mysql.connector.errors.IntegrityError:
            return jsonify({"error": "Email already registered"}), 409
        finally:
            cursor.close()
            db.close()
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/auth/login", methods=["POST", "OPTIONS"])
def login():
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"}), 200
    
    try:
        data = request.json
        email = data.get("email", "").strip().lower()
        password = data.get("password", "")

        db = get_db()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
        user = cursor.fetchone()
        cursor.close()
        db.close()

        if not user or not bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
            return jsonify({"error": "Invalid email or password"}), 401

        token = jwt.encode({
            "user_id": user["id"],
            "email": user["email"],
            "exp": datetime.datetime.utcnow() + datetime.timedelta(days=30)
        }, SECRET_KEY, algorithm="HS256")

        return jsonify({
            "token": token,
            "user_id": user["id"],
            "email": user["email"],
            "full_name": user["full_name"],
            "trust_score": float(user["trust_score"] or 0),
            "role": user["role"]
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/auth/me", methods=["GET", "OPTIONS"])
def me():
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"}), 200
    
    try:
        payload = decode_token(request)
        if not payload:
            return jsonify({"error": "Unauthorized"}), 401

        db = get_db()
        cursor = db.cursor(dictionary=True)
        cursor.execute(
            "SELECT id, email, full_name, role, trust_score, profile_completeness, account_created_at FROM users WHERE id = %s",
            (payload["user_id"],)
        )
        user = cursor.fetchone()

        if user:
            try:
                cursor.execute("SELECT points_balance FROM reward_points WHERE user_id = %s", (payload["user_id"],))
                pts = cursor.fetchone()
                user["points_balance"] = pts["points_balance"] if pts else 0
            except:
                user["points_balance"] = 0
                
            user["trust_score"] = float(user["trust_score"] or 0)

        cursor.close()
        db.close()

        if not user:
            return jsonify({"error": "User not found"}), 404

        return jsonify(user)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/shops", methods=["GET", "OPTIONS"])
def get_shops():
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"}), 200
    
    try:
        category = request.args.get("category")
        search = request.args.get("search")

        db = get_db()
        cursor = db.cursor(dictionary=True)
        query = """
            SELECT s.*, u.full_name as owner_name, COUNT(r.id) as review_count
            FROM shops s
            LEFT JOIN users u ON s.owner_id = u.id
            LEFT JOIN reviews r ON r.shop_id = s.id
        """
        params = []

        if category:
            query += " WHERE s.category = %s"
            params.append(category)
        if search:
            query += (" AND" if category else " WHERE") + " s.name LIKE %s"
            params.append(f"%{search}%")

        query += " GROUP BY s.id ORDER BY s.trust_score DESC"
        cursor.execute(query, params)
        shops = cursor.fetchall()
        for shop in shops:
            shop["trust_score"] = float(shop["trust_score"] or 0)
        cursor.close()
        db.close()
        return jsonify(shops)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/shops/<shop_id>", methods=["GET", "OPTIONS"])
def get_shop(shop_id):
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"}), 200
    
    try:
        db = get_db()
        cursor = db.cursor(dictionary=True)
        cursor.execute(
            "SELECT s.*, u.full_name as owner_name FROM shops s LEFT JOIN users u ON s.owner_id = u.id WHERE s.id = %s",
            (shop_id,)
        )
        shop = cursor.fetchone()
        cursor.close()
        db.close()
        if not shop:
            return jsonify({"error": "Shop not found"}), 404
        shop["trust_score"] = float(shop["trust_score"] or 0)
        return jsonify(shop)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/reviews", methods=["GET", "OPTIONS"])
def get_reviews():
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"}), 200
    
    try:
        shop_id = request.args.get("shop_id")
        if not shop_id:
            return jsonify([])
        db = get_db()
        cursor = db.cursor(dictionary=True)
        cursor.execute(
            """SELECT r.*, u.full_name as reviewer_name, u.trust_score as reviewer_trust_score
               FROM reviews r
               LEFT JOIN users u ON r.user_id = u.id
               WHERE r.shop_id = %s AND r.is_approved = 1
               ORDER BY r.created_at DESC""",
            (shop_id,)
        )
        reviews = cursor.fetchall()
        for r in reviews:
            r["reviewer_trust_score"] = float(r["reviewer_trust_score"] or 0)
        cursor.close()
        db.close()
        return jsonify(reviews)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))