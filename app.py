from flask import Flask, request, jsonify
from flask_cors import CORS
import mysql.connector
import bcrypt
import jwt
import datetime
import os
 
app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": ["https://trustpulse-web.vercel.app", "http://localhost:5173"]}})
 
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
 
@app.route("/auth/signup", methods=["POST"])
def signup():
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
        cursor.execute("INSERT INTO reward_points (user_id, points_balance) VALUES (%s, 0)", (user_id,))
        db.commit()
 
        token = jwt.encode({
            "user_id": user_id,
            "email": email,
            "exp": datetime.datetime.utcnow() + datetime.timedelta(days=30)
        }, SECRET_KEY, algorithm="HS256")
 
        return jsonify({"token": token, "user_id": user_id, "email": email, "full_name": full_name})
    except mysql.connector.errors.IntegrityError:
        return jsonify({"error": "Email already registered"}), 409
    finally:
        cursor.close()
        db.close()
 
@app.route("/auth/login", methods=["POST"])
def login():
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
 
@app.route("/auth/me", methods=["GET"])
def me():
    payload = decode_token(request)
    if not payload:
        return jsonify({"error": "Unauthorized"}), 401
 
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute(
        "SELECT id, email, full_name, role, trust_score, profile_completeness, account_created_at, account_type FROM users WHERE id = %s",
        (payload["user_id"],)
    )
    user = cursor.fetchone()
 
    cursor.execute("SELECT points_balance FROM reward_points WHERE user_id = %s", (payload["user_id"],))
    pts = cursor.fetchone()
    cursor.close()
    db.close()
 
    if not user:
        return jsonify({"error": "User not found"}), 404
 
    user["points_balance"] = pts["points_balance"] if pts else 0
    user["trust_score"] = float(user["trust_score"] or 0)
    return jsonify(user)
 
@app.route("/auth/update-account-type", methods=["POST"])
def update_account_type():
    payload = decode_token(request)
    if not payload:
        return jsonify({"error": "Unauthorized"}), 401
    
    data = request.json
    account_type = data.get("account_type")
    
    if account_type not in ['user', 'shop_owner']:
        return jsonify({"error": "Invalid account type"}), 400
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("UPDATE users SET account_type = %s WHERE id = %s", (account_type, payload["user_id"]))
    db.commit()
    cursor.close()
    db.close()
    
    return jsonify({"success": True, "account_type": account_type})
 
@app.route("/shops", methods=["GET"])
def get_shops():
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
 
# ── Fixed: accepts both UUID strings and integer IDs ─────────────────────────
@app.route("/shops/<shop_id>", methods=["GET"])
def get_shop(shop_id):
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute(
        "SELECT s.*, u.full_name as owner_name FROM shops s LEFT JOIN users u ON s.owner_id = u.id WHERE s.id = %s",
        (shop_id,)
    )
    shop = cursor.fetchone()
    if not shop:
        cursor.close()
        db.close()
        return jsonify({"error": "Shop not found"}), 404
 
    cursor.execute(
        """SELECT r.*, u.full_name as reviewer_name, u.trust_score as reviewer_trust
           FROM reviews r
           LEFT JOIN users u ON r.user_id = u.id
           WHERE r.shop_id = %s AND r.is_approved = 1
           ORDER BY r.created_at DESC""",
        (shop_id,)
    )
    shop["reviews"] = cursor.fetchall()
    shop["trust_score"] = float(shop["trust_score"] or 0)
 
    # Add computed fields for frontend
    reviews = shop["reviews"]
    shop["review_count"] = len(reviews)
    shop["average_rating"] = round(
        sum(r["rating"] for r in reviews) / len(reviews), 1
    ) if reviews else 0
    shop["verified_review_count"] = sum(1 for r in reviews if r.get("is_verified"))
 
    cursor.close()
    db.close()
    return jsonify(shop)
 
 
@app.route("/shops/<shop_id>", methods=["PATCH"])
def update_shop(shop_id):
    payload = decode_token(request)
    if not payload:
        return jsonify({"error": "Unauthorized"}), 401
    data = request.json or {}
    allowed = ["name", "description", "category", "platform", "profile_url", "flagged"]
    updates = {k: v for k, v in data.items() if k in allowed}
    if not updates:
        return jsonify({"error": "Nothing to update"}), 400
    db = get_db()
    cursor = db.cursor(dictionary=True)
    set_clause = ", ".join(f"{k} = %s" for k in updates)
    cursor.execute(f"UPDATE shops SET {set_clause} WHERE id = %s", (*updates.values(), shop_id))
    db.commit()
    cursor.execute("SELECT * FROM shops WHERE id = %s", (shop_id,))
    shop = cursor.fetchone()
    cursor.close()
    db.close()
    if shop:
        shop["trust_score"] = float(shop["trust_score"] or 0)
    return jsonify(shop or {})
 
@app.route("/auth/me", methods=["PATCH"])
def update_me():
    payload = decode_token(request)
    if not payload:
        return jsonify({"error": "Unauthorized"}), 401
    data = request.json or {}
    allowed = ["full_name", "display_name", "username", "profile_completeness", "profile_image", "owned_shop_id"]
    updates = {k: v for k, v in data.items() if k in allowed}
    if not updates:
        return jsonify({"error": "Nothing to update"}), 400
    db = get_db()
    cursor = db.cursor(dictionary=True)
    set_clause = ", ".join(f"{k} = %s" for k in updates)
    cursor.execute(f"UPDATE users SET {set_clause} WHERE id = %s", (*updates.values(), payload["user_id"]))
    db.commit()
    cursor.execute(
        "SELECT id, email, full_name, role, trust_score, profile_completeness, account_created_at FROM users WHERE id = %s",
        (payload["user_id"],)
    )
    user = cursor.fetchone()
    cursor.execute("SELECT points_balance FROM reward_points WHERE user_id = %s", (payload["user_id"],))
    pts = cursor.fetchone()
    cursor.close()
    db.close()
    if user:
        user["points_balance"] = pts["points_balance"] if pts else 0
        user["trust_score"] = float(user["trust_score"] or 0)
    return jsonify(user or {})
 
@app.route("/reviews/<int:review_id>", methods=["PATCH"])
def update_review(review_id):
    payload = decode_token(request)
    if not payload:
        return jsonify({"error": "Unauthorized"}), 401
    data = request.json or {}
    allowed = ["likes", "reported", "comments_count"]
    updates = {k: v for k, v in data.items() if k in allowed}
    if not updates:
        return jsonify({"error": "Nothing to update"}), 400
    db = get_db()
    cursor = db.cursor(dictionary=True)
    set_clause = ", ".join(f"{k} = %s" for k in updates)
    cursor.execute(f"UPDATE reviews SET {set_clause} WHERE id = %s", (*updates.values(), review_id))
    db.commit()
    cursor.close()
    db.close()
    return jsonify({"success": True})
 
@app.route("/reviews/<int:review_id>", methods=["DELETE"])
def delete_review(review_id):
    payload = decode_token(request)
    if not payload:
        return jsonify({"error": "Unauthorized"}), 401
    db = get_db()
    cursor = db.cursor()
    cursor.execute("DELETE FROM reviews WHERE id = %s", (review_id,))
    db.commit()
    cursor.close()
    db.close()
    return jsonify({"success": True})
 
@app.route("/shops/<shop_id>", methods=["DELETE"])
def delete_shop(shop_id):
    payload = decode_token(request)
    if not payload:
        return jsonify({"error": "Unauthorized"}), 401
    db = get_db()
    cursor = db.cursor()
    cursor.execute("DELETE FROM reviews WHERE shop_id = %s", (shop_id,))
    cursor.execute("DELETE FROM shops WHERE id = %s", (shop_id,))
    db.commit()
    cursor.close()
    db.close()
    return jsonify({"success": True})
 
@app.route("/reviews", methods=["GET"])
def get_reviews():
    shop_id = request.args.get("shop_id")
    user_id = request.args.get("user_id")
 
    db = get_db()
    cursor = db.cursor(dictionary=True)
 
    query = """
        SELECT r.*, u.full_name as reviewer_name, s.name as shop_name
        FROM reviews r
        LEFT JOIN users u ON r.user_id = u.id
        LEFT JOIN shops s ON r.shop_id = s.id
        WHERE r.is_approved = 1
    """
    params = []
    if shop_id:
        query += " AND r.shop_id = %s"
        params.append(shop_id)
    if user_id:
        query += " AND r.user_id = %s"
        params.append(user_id)
 
    query += " ORDER BY r.created_at DESC LIMIT 100"
    cursor.execute(query, params)
    reviews = cursor.fetchall()
    cursor.close()
    db.close()
    return jsonify(reviews)
 
@app.route("/reviews", methods=["POST"])
def submit_review():
    payload = decode_token(request)
    if not payload:
        return jsonify({"error": "Login required"}), 401
 
    data = request.json
    user_id = payload["user_id"]
    shop_id = data.get("shop_id")
    rating = data.get("rating")
    review_text = data.get("review_text", "")
    evidence_url = data.get("evidence_url", "")
 
    if not shop_id or not rating:
        return jsonify({"error": "shop_id and rating are required"}), 400
    if not (1 <= int(rating) <= 5):
        return jsonify({"error": "Rating must be 1-5"}), 400
 
    db = get_db()
    cursor = db.cursor(dictionary=True)
 
    cursor.execute("SELECT id FROM reviews WHERE user_id = %s AND shop_id = %s", (user_id, shop_id))
    if cursor.fetchone():
        cursor.close()
        db.close()
        return jsonify({"error": "You already reviewed this shop"}), 409
 
    cursor.execute("SELECT owner_id FROM shops WHERE id = %s", (shop_id,))
    shop = cursor.fetchone()
    if shop and shop["owner_id"] == user_id:
        cursor.close()
        db.close()
        return jsonify({"error": "Shop owners cannot review their own shop"}), 403
 
    is_verified = 1 if evidence_url else 0
    cursor.execute(
        "INSERT INTO reviews (user_id, shop_id, rating, review_text, evidence_url, is_verified, is_approved) VALUES (%s,%s,%s,%s,%s,%s,1)",
        (user_id, shop_id, rating, review_text, evidence_url, is_verified)
    )
    db.commit()
 
    points = 25 if is_verified else 10
    cursor.execute(
        "UPDATE reward_points SET points_balance = points_balance + %s, last_updated = NOW() WHERE user_id = %s",
        (points, user_id)
    )
    db.commit()
 
    recalculate_shop_trust(shop_id, cursor, db)
    recalculate_user_trust(user_id, cursor, db)
 
    cursor.close()
    db.close()
    return jsonify({"message": "Review submitted", "points_earned": points, "is_verified": bool(is_verified)})
 
def recalculate_shop_trust(shop_id, cursor, db):
    cursor.execute("""
        SELECT AVG(r.rating) as avg_rating, COUNT(r.id) as review_count,
               TIMESTAMPDIFF(MONTH, s.created_at, NOW()) as age_months, s.license_verified
        FROM shops s
        LEFT JOIN reviews r ON r.shop_id = s.id AND r.is_approved = 1
        WHERE s.id = %s
        GROUP BY s.id, s.created_at, s.license_verified
    """, (shop_id,))
    row = cursor.fetchone()
    if not row or not row["avg_rating"]:
        return
 
    rating_score  = ((float(row["avg_rating"]) - 1) / 4) * 100
    review_score  = min(float(row["review_count"]) / 50, 1) * 100
    age_score     = min(float(row["age_months"] or 0) / 24, 1) * 100
    license_score = 100 if row["license_verified"] else 0
 
    trust = (rating_score * 0.40) + (review_score * 0.30) + (age_score * 0.20) + (license_score * 0.10)
    cursor.execute("UPDATE shops SET trust_score = %s WHERE id = %s", (round(trust, 1), shop_id))
    db.commit()
 
def recalculate_user_trust(user_id, cursor, db):
    cursor.execute("""
        SELECT AVG(r.rating) as avg_rating, COUNT(r.id) as review_count,
               TIMESTAMPDIFF(MONTH, u.account_created_at, NOW()) as age_months, u.profile_completeness
        FROM users u
        LEFT JOIN reviews r ON r.user_id = u.id
        WHERE u.id = %s
        GROUP BY u.id, u.account_created_at, u.profile_completeness
    """, (user_id,))
    row = cursor.fetchone()
    if not row:
        return
 
    rating_score  = 100 - (abs(float(row["avg_rating"] or 3) - 3) / 2 * 100)
    review_score  = min(float(row["review_count"] or 0) / 30, 1) * 100
    age_score     = min(float(row["age_months"] or 0) / 24, 1) * 100
    profile_score = float(row["profile_completeness"] or 0)
 
    trust = (rating_score * 0.40) + (review_score * 0.30) + (age_score * 0.20) + (profile_score * 0.10)
    cursor.execute("UPDATE users SET trust_score = %s WHERE id = %s", (round(trust, 1), user_id))
    db.commit()
 
@app.route("/trust/shop/<shop_id>", methods=["GET"])
def trust_breakdown_shop(shop_id):
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("""
        SELECT AVG(r.rating) as avg_rating, COUNT(r.id) as review_count,
               TIMESTAMPDIFF(MONTH, s.created_at, NOW()) as age_months,
               s.license_verified, s.trust_score
        FROM shops s
        LEFT JOIN reviews r ON r.shop_id = s.id AND r.is_approved = 1
        WHERE s.id = %s
        GROUP BY s.id
    """, (shop_id,))
    row = cursor.fetchone()
    cursor.close()
    db.close()
 
    if not row:
        return jsonify({"error": "Shop not found"}), 404
 
    avg_rating   = float(row["avg_rating"] or 0)
    review_count = int(row["review_count"] or 0)
    age_months   = int(row["age_months"] or 0)
 
    return jsonify({
        "total_trust_score": float(row["trust_score"] or 0),
        "components": {
            "rating":  {"label": "Average rating",    "raw": round(avg_rating, 2), "score": round(((avg_rating - 1) / 4) * 100 * 0.40, 1), "weight": "40%"},
            "reviews": {"label": "Number of reviews", "raw": review_count,          "score": round(min(review_count / 50, 1) * 100 * 0.30, 1), "weight": "30%"},
            "age":     {"label": "Account age",       "raw": f"{age_months} months","score": round(min(age_months / 24, 1) * 100 * 0.20, 1), "weight": "20%"},
            "license": {"label": "License verified",  "raw": bool(row["license_verified"]), "score": 10 if row["license_verified"] else 0, "weight": "10%"}
        }
    })
 
@app.route("/complaints", methods=["POST"])
def submit_complaint():
    payload = decode_token(request)
    if not payload:
        return jsonify({"error": "Login required"}), 401
 
    data = request.json
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "INSERT INTO complaints (user_id, shop_id, complaint_text, status) VALUES (%s,%s,%s,'pending')",
        (payload["user_id"], data.get("shop_id"), data.get("complaint_text"))
    )
    db.commit()
    cursor.close()
    db.close()
    return jsonify({"message": "Complaint submitted", "status": "pending"})
 
@app.route("/complaints", methods=["GET"])
def get_complaints():
    payload = decode_token(request)
    if not payload:
        return jsonify({"error": "Login required"}), 401
 
    shop_id = request.args.get("shop_id")
    db = get_db()
    cursor = db.cursor(dictionary=True)
 
    if shop_id:
        cursor.execute(
            "SELECT c.*, s.name as shop_name FROM complaints c LEFT JOIN shops s ON c.shop_id = s.id WHERE c.shop_id = %s ORDER BY c.created_at DESC",
            (shop_id,)
        )
    else:
        cursor.execute(
            "SELECT c.*, s.name as shop_name FROM complaints c LEFT JOIN shops s ON c.shop_id = s.id WHERE c.user_id = %s ORDER BY c.created_at DESC",
            (payload["user_id"],)
        )
    complaints = cursor.fetchall()
    cursor.close()
    db.close()
    return jsonify(complaints)
 
@app.route("/leaderboard", methods=["GET"])
def leaderboard():
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("""
        SELECT u.id, u.full_name, u.trust_score, u.role, rp.points_balance, COUNT(r.id) as review_count
        FROM users u
        LEFT JOIN reward_points rp ON rp.user_id = u.id
        LEFT JOIN reviews r ON r.user_id = u.id
        GROUP BY u.id
        ORDER BY rp.points_balance DESC
        LIMIT 20
    """)
    users = cursor.fetchall()
    for u in users:
        u["trust_score"] = float(u["trust_score"] or 0)
    cursor.close()
    db.close()
    return jsonify(users)
 
 
@app.route("/comments", methods=["POST"])
def add_comment():
    payload = decode_token(request)
    if not payload:
        return jsonify({"error": "Login required"}), 401
    data = request.json
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute(
        "INSERT INTO comments (review_id, user_id, commenter_name, commenter_email, comment_text) VALUES (%s, %s, %s, %s, %s)",
        (data.get("review_id"), payload["user_id"], data.get("commenter_name"), data.get("commenter_email"), data.get("comment_text"))
    )
    db.commit()
    cursor.close()
    db.close()
    return jsonify({"message": "Comment added"})
 
@app.route("/comments", methods=["GET"])
def get_comments():
    review_id = request.args.get("review_id")
    if not review_id:
        return jsonify([])
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute(
        "SELECT * FROM comments WHERE review_id = %s ORDER BY created_at ASC",
        (review_id,)
    )
    comments = cursor.fetchall()
    cursor.close()
    db.close()
    return jsonify(comments)
 
@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "TrustPulse API running"})
 
if __name__ == "__main__":
    app.run(debug=True, port=5000)