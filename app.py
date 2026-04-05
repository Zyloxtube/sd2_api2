from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import time
import re
import random
import string
import threading
import uuid
import json
import os
import redis
from html.parser import HTMLParser

app = Flask(__name__)
CORS(app)

# 🔥 KV (Upstash Redis)
r = redis.from_url(os.environ.get("REDIS_URL"))

# ---------- Guerrilla Mail ----------
API_BASE = 'https://api.guerrillamail.com/ajax.php'
DOMAINS = ['sharklasers.com']

def generate_temp_email():
    res = requests.get(f"{API_BASE}?f=get_email_address")
    data = res.json()

    email = data["email_addr"]
    token = data["sid_token"]

    local = email.split("@")[0]
    return f"{local}@{DOMAINS[0]}", token

def generate_password():
    return random.choice(string.ascii_uppercase) + ''.join(
        random.choices(string.ascii_lowercase, k=3)
    ) + str(random.randint(1000, 9999))

def send_code(email):
    requests.post(
        "https://api.buzzy.now/api/v1/user/send-email-code",
        json={"email": email, "type": 1}
    )

# ---------- HTML ----------
class HTMLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self.data = []

    def handle_data(self, d):
        self.data.append(d)

    def get(self):
        return " ".join(self.data)

def strip_html(html):
    s = HTMLStripper()
    s.feed(html)
    return s.get()

def extract_code(text):
    if not text:
        return None
    m = re.search(r'\d{4,6}', text)
    return m.group() if m else None

def wait_code(token):
    seen = set()

    for _ in range(30):
        res = requests.get(f"{API_BASE}?f=check_email&sid_token={token}")
        data = res.json()

        for mail in data.get("list", []):
            if mail["mail_id"] in seen:
                continue
            seen.add(mail["mail_id"])

            code = extract_code(mail.get("mail_subject"))

            if not code:
                try:
                    full = requests.get(
                        f"{API_BASE}?f=fetch_email&email_id={mail['mail_id']}&sid_token={token}"
                    ).json()
                    body = full.get("mail_body", "")
                    code = extract_code(strip_html(body))
                except:
                    pass

            if code:
                return code

        time.sleep(4)

    return None

# ---------- REGISTER ----------
def register(email, password, code):
    res = requests.post(
        "https://api.buzzy.now/api/v1/user/register",
        json={"email": email, "password": password, "emailCode": code}
    )
    data = res.json()

    if data.get("code") != 200:
        raise Exception("Register failed")

    return data["data"]["token"]

# ---------- PROJECT ----------
def create_project(token, prompt):
    res = requests.post(
        "https://api.buzzy.now/api/app/v1/project/create",
        json={
            "name": "Untitled",
            "workflowType": "SOTA",
            "instructionSegments": [{"type": "text", "content": prompt}],
            "duration": 10,
            "aspectRatio": "16:9",
            "prompt": prompt
        },
        headers={"Authorization": f"Bearer {token}"}
    )

    return res.json()["data"]["id"]

def wait_video(token, project_id):
    while True:
        res = requests.get(
            "https://api.buzzy.now/api/app/v1/project/list?pageNumber=1&pageSize=100",
            headers={"Authorization": f"Bearer {token}"}
        )

        data = res.json()

        for p in data.get("data", {}).get("records", []):
            if p["id"] == project_id:
                if p["status"] == "completed":
                    return p["results"][0]["videoUrl"]

                if p["status"] == "failed":
                    raise Exception("Video failed")

        time.sleep(5)

# ---------- KV HELPERS ----------
def save_job(job_id, data):
    r.set(job_id, json.dumps(data))

def get_job(job_id):
    data = r.get(job_id)
    return json.loads(data) if data else None

# ---------- PIPELINE ----------
def full_pipeline(prompt, job_id):
    try:
        email, token = generate_temp_email()
        password = generate_password()

        send_code(email)
        code = wait_code(token)

        if not code:
            raise Exception("No code received")

        user_token = register(email, password, code)
        project_id = create_project(user_token, prompt)

        video = wait_video(user_token, project_id)

        save_job(job_id, {"status": "completed", "video_url": video})

    except Exception as e:
        save_job(job_id, {"status": "failed", "error": str(e)})

# ---------- API ----------

@app.route('/generate', methods=['GET'])
def generate():
    prompt = request.args.get("prompt")

    if not prompt:
        return jsonify({"error": "Missing prompt"}), 400

    job_id = str(uuid.uuid4())

    # save initial state in KV
    save_job(job_id, {"status": "processing"})

    # background thread
    threading.Thread(target=full_pipeline, args=(prompt, job_id)).start()

    return jsonify({"jobId": job_id})

@app.route('/status', methods=['GET'])
def status():
    job_id = request.args.get("jobid")

    if not job_id:
        return jsonify({"error": "Missing jobid"}), 400

    job = get_job(job_id)

    if not job:
        return jsonify({"error": "Not found"}), 404

    return jsonify(job)

@app.route('/')
def home():
    return jsonify({"message": "API running 24/7 🚀"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
