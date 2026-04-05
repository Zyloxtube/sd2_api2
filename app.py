from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import time
import re
import random
import string
import os
from html.parser import HTMLParser
import threading
import uuid
from datetime import datetime
import logging

app = Flask(__name__)
CORS(app)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Store jobs in memory
jobs = {}

# ========== Guerrilla Mail Functions ==========
DOMAIN_OPTIONS = ['sharklasers.com', 'guerrillamail.net', 'guerrillamail.com']
API_BASE = 'https://api.guerrillamail.com/ajax.php'

def generate_temp_email():
    response = requests.get(f"{API_BASE}?f=get_email_address")
    data = response.json()
    if 'email_addr' not in data:
        raise Exception(f"Failed to generate temp email. Response: {data}")
    sid_token = data['sid_token']
    local_part = data['email_addr'].split('@')[0]
    email = f"{local_part}@{DOMAIN_OPTIONS[0]}"
    return email, sid_token

def generate_random_password():
    upper = random.choice(string.ascii_uppercase)
    lower = ''.join(random.choices(string.ascii_lowercase, k=3))
    nums = str(random.randint(1000, 9999))
    return upper + lower + nums

def send_verification_code(email):
    response = requests.post(
        'https://api.buzzy.now/api/v1/user/send-email-code',
        json={'email': email, 'type': 1},
        headers={'Content-Type': 'application/json'}
    )
    data = response.json()
    if data.get('code') != 200:
        raise Exception(f"Failed to send verification code. Response: {data}")
    return True

class _HTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts = []

    def handle_data(self, data):
        self._parts.append(data)

    def get_text(self):
        return ' '.join(self._parts)

def strip_html(html):
    if not html:
        return ''
    parser = _HTMLTextExtractor()
    try:
        parser.feed(html)
        return parser.get_text()
    except Exception:
        return html

def extract_code_from_text(text):
    if not text:
        return None
    m = re.search(r'(\d{6})', text)
    if m:
        return m.group(1)
    m = re.search(r'(\d{5})', text)
    if m:
        return m.group(1)
    m = re.search(r'(?:verification\s+code|verification|code|otp)[^\d]{0,20}?(\d{4})', text, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r'(\d{4})', text)
    return m.group(1) if m else None

def wait_for_code(sid_token, max_attempts=30, interval=4):
    current_seq = 0
    seen_ids = set()
    for attempt in range(max_attempts):
        response = requests.get(
            f"{API_BASE}?f=check_email&sid_token={sid_token}&seq={current_seq}"
        )
        data = response.json()
        if 'seq' in data:
            current_seq = data['seq']

        for mail in data.get('list', []):
            mail_id = mail.get('mail_id')
            if mail_id in seen_ids:
                continue
            seen_ids.add(mail_id)

            code = (
                extract_code_from_text(mail.get('mail_subject', '')) or
                extract_code_from_text(mail.get('mail_from', ''))
            )

            if not code:
                try:
                    full = requests.get(
                        f"{API_BASE}?f=fetch_email&email_id={mail_id}&sid_token={sid_token}"
                    ).json()
                    body = full.get('mail_body', '') or full.get('mail_excerpt', '')
                    code = (
                        extract_code_from_text(strip_html(body)) or
                        extract_code_from_text(body)
                    )
                except Exception:
                    pass

            if code:
                return code

        time.sleep(interval)
    return None

def register_user(email, password, email_code):
    response = requests.post(
        'https://api.buzzy.now/api/v1/user/register',
        json={'email': email, 'password': password, 'emailCode': email_code},
        headers={'Content-Type': 'application/json'}
    )
    data = response.json()
    if data.get('code') == 200:
        return data['data']['token']
    raise Exception(f"Registration failed. Response: {data}")

def create_video_project(token, prompt):
    response = requests.post(
        'https://api.buzzy.now/api/app/v1/project/create',
        json={
            'name': 'Untitled',
            'workflowType': 'SOTA',
            'instructionSegments': [{'type': 'text', 'content': prompt}],
            'imageUrls': [],
            'duration': 10,
            'aspectRatio': '16:9',
            'prompt': prompt
        },
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {token}'
        }
    )
    data = response.json()
    if data.get('code') == 201:
        return data['data']['id']
    raise Exception(f"Failed to create video project. Response: {data}")

def poll_for_video(token, project_id, status_callback=None, interval=5):
    poll_count = 0
    last_status = None

    while True:
        poll_count += 1

        response = requests.get(
            'https://api.buzzy.now/api/app/v1/project/list?pageNumber=1&pageSize=100',
            headers={
                'Authorization': f'Bearer {token}',
                'accept': 'application/json, text/plain, */*'
            }
        )

        data = response.json()

        if data.get('code') != 200:
            if status_callback:
                status_callback(f"Retrying... API code: {data.get('code')}")
            time.sleep(interval)
            continue

        records = data.get('data', {}).get('records', [])
        target = next((p for p in records if p.get('id') == project_id), None)

        if target:
            status = target.get('status', 'unknown')

            if status != last_status:
                if status_callback:
                    status_callback(f"Status = {status}")
                last_status = status

            if status in ['success', 'completed']:
                results = target.get('results', [])
                if results:
                    return results[0].get('videoUrl')

                video_urls = target.get('videoUrls', [])
                if video_urls:
                    return video_urls[0]

            elif status == 'failed':
                raise Exception("Video generation failed")

        time.sleep(interval)

def run_full_pipeline(prompt, status_callback=None):
    email, sid_token = generate_temp_email()
    password = generate_random_password()
    send_verification_code(email)
    code = wait_for_code(sid_token)

    if not code:
        raise Exception("No verification code received")

    token = register_user(email, password, code)
    project_id = create_video_project(token, prompt)
    return poll_for_video(token, project_id, status_callback)

# ========== API ==========

@app.route('/generate', methods=['POST'])
def generate_video():
    data = request.get_json()

    if not data or 'prompt' not in data:
        return jsonify({'error': 'Missing prompt'}), 400

    job_id = str(uuid.uuid4())

    jobs[job_id] = {
        'status': 'processing',
        'video_url': None,
        'error': None
    }

    def task():
        try:
            video_url = run_full_pipeline(data['prompt'])
            jobs[job_id]['status'] = 'completed'
            jobs[job_id]['video_url'] = video_url
        except Exception as e:
            jobs[job_id]['status'] = 'failed'
            jobs[job_id]['error'] = str(e)

    threading.Thread(target=task).start()

    return jsonify({'jobId': job_id})

@app.route('/status/<job_id>', methods=['GET'])
def status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(job)

@app.route('/')
def home():
    return jsonify({'message': 'API running 24/7 🚀'})