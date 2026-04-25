from flask import Flask, jsonify, request
from flask_cors import CORS
import mysql.connector
from datetime import datetime

app = Flask(__name__)
CORS(app)

HOST = 'gondola.proxy.rlwy.net'
PORT = 37338
USER = 'root'
PASSWORD = 'rCGgmsQSBGOncYmGnpNdhSHirCkOktNm'
DATABASE = 'railway'

def get_db():
    return mysql.connector.connect(host=HOST, port=PORT, user=USER, password=PASSWORD, database=DATABASE, connection_timeout=30)

@app.route('/', methods=['GET'])
def health():
    return jsonify({'status': 'TrustPulse API is running'})

@app.route('/shops', methods=['GET'])
def get_shops():
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM shops ORDER BY trust_score DESC")
    shops = cursor.fetchall()
    cursor.close()
    db.close()
    return jsonify(shops)

@app.route('/users/login', methods=['POST'])
def login_user():
    data = request.get_json()
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT id, email, full_name, role, trust_score FROM users WHERE email = %s AND password_hash = %s", (data.get('email'), data.get('password_hash')))
    user = cursor.fetchone()
    cursor.close()
    db.close()
    if user:
        return jsonify(user)
    return jsonify({'error': 'Invalid email or password'}), 401

@app.route('/reviews', methods=['POST'])
def submit_review():
    data = request.get_json()
    rating = data.get('rating', 0)
    if not (1 <= rating <= 5):
        return jsonify({'error': 'Rating must be between 1 and 5'}), 400
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT id FROM reviews WHERE user_id = %s AND shop_id = %s", (data.get('user_id'), data.get('shop_id')))
    if cursor.fetchone():
        cursor.close()
        db.close()
        return jsonify({'error': 'You have already reviewed this shop'}), 409
    evidence_url = data.get('evidence_url')
    cursor.execute("INSERT INTO reviews (id, user_id, shop_id, rating, review_text, evidence_url, is_verified) VALUES (UUID(), %s, %s, %s, %s, %s, %s)", (data.get('user_id'), data.get('shop_id'), rating, data.get('review_text'), evidence_url, True if evidence_url else False))
    db.commit()
    cursor.close()
    db.close()
    return jsonify({'message': 'Review submitted successfully'}), 201

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
