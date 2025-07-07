from flask import Flask, render_template, request, jsonify, redirect, url_for
import requests
import time
import os
import datetime
import threading
import json
import logging
from flask_socketio import SocketIO, emit
from dotenv import load_dotenv

# Load .env file for local testing
load_dotenv()

# === Logging Setup ===
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

# === Flask App Setup ===
app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'your-secret-key-here')
socketio = SocketIO(app, cors_allowed_origins="*")

# === Telegram Bot Setup ===
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# Log Telegram credentials status at startup
if not BOT_TOKEN or not CHAT_ID:
    logging.warning("⚠️ Telegram credentials not fully configured!")
    if not BOT_TOKEN:
        logging.warning("Missing BOT_TOKEN in environment variables")
    if not CHAT_ID:
        logging.warning("Missing CHAT_ID in environment variables")
else:
    logging.info("✅ Telegram credentials found in environment variables")

# === Cinema Code Map ===
CINEMA_CODES = {
    "Grand Mall": "389",
    "Palazzo": "388",
    "Grand Galada": "400",
    "SKLS Galaxy Mall": "410",
    "Heritage RSL": "417",
    "Marina Mall": "232",
    "Luxe": "320",
    "Escape": "359",
    "Sathyam": "331",
    "Aerohub": "432",
    "Ampa": "358",
    "VR": "523"
}

# === Screen Names for Each Theatre ===
THEATRE_SCREENS = {
    "Grand Mall": ["AUDI 01", "AUDI 02", "AUDI 03", "AUDI 04", "AUDI 05"],
    "Palazzo": ["AUDI 1", "AUDI 2", "AUDI 3", "AUDI 4", "AUDI 5", "AUDI 6", "AUDI 7", "AUDI 8", "AUDI 9"],
    "Grand Galada": ["AUDI 01", "AUDI 02", "AUDI 03", "AUDI 04", "AUDI 05"],
    "SKLS Galaxy Mall": ["AUDI 01", "AUDI 02", "AUDI 03", "AUDI 04", "AUDI 05"],
    "Heritage RSL": ["AUDI 01", "AUDI 02", "AUDI 03", "AUDI 04", "AUDI 05", "AUDI 06", "AUDI 07", "AUDI 08", "AUDI 09", "AUDI 10"],
    "Marina Mall": ["LASER 4", "SCREEN 1", "SCREEN 2", "SCREEN 3", "SCREEN 5", "SCREEN 6", "SCREEN 7", "SCREEN 8"],
    "Luxe": ["SCREEN 1", "SCREEN 2", "SCREEN 3", "SCREEN 4", "SCREEN 5", "SCREEN 6", "SCREEN 7", "SCREEN 8", "SCREEN 9", "SCREEN 10", "SCREEN 11"],
    "Escape": ["AUDI 1 BLUSH", "AUDI 2 WEAVE", "AUDI 3 SPOT", "AUDI 4 STREAK", "AUDI 5 PLUSH", "AUDI 6 FRAME", "AUDI 7 CARVE", "AUDI 8 KITES"],
    "Sathyam": ["6DEGREES", "SANTHAM", "SATHYAM", "SEASONS", "SERENE", "STUDIO-5"],
    "Aerohub": ["AUDI 01", "AUDI 02", "AUDI 03", "AUDI 04", "AUDI 05"],
    "Ampa": ["AUDI 01", "AUDI 02", "AUDI 03", "AUDI 04", "AUDI 05", "AUDI 06", "AUDI 07"],
    "VR": ["AUDI 01", "AUDI 02", "AUDI 03", "AUDI 04", "AUDI 05", "AUDI 06", "AUDI 07", "AUDI 08", "AUDI 09", "AUDI 10"]
}

# === Global Variables ===
alert_sent_map = {}
monitoring_flag = threading.Event()
monitoring_threads = []
CHECK_INTERVAL = 60  # seconds
logs = []

def log_message(msg):
    timestamp = datetime.datetime.now().strftime('%H:%M:%S')
    log_entry = f"{timestamp} - {msg}"
    logs.append(log_entry)
    if len(logs) > 100:  # Keep only last 100 logs
        logs.pop(0)
    logging.info(msg)
    socketio.emit('log_update', {'message': log_entry})

def send_telegram(msg):
    """Send message via Telegram bot with proper token handling"""
    if not BOT_TOKEN or not CHAT_ID:
        log_message("❌ Telegram not configured - missing BOT_TOKEN or CHAT_ID")
        return False

    try:
        base_url = f"https://api.telegram.org/bot{BOT_TOKEN}"
        send_message_url = f"{base_url}/sendMessage"
        
        params = {
            "chat_id": CHAT_ID,
            "text": msg,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        
        log_message(f"📤 Sending Telegram message to chat {CHAT_ID}")
        response = requests.post(send_message_url, json=params, timeout=10)
        response.raise_for_status()  # Raises exception for 4XX/5XX status codes
        
        log_message("✅ Telegram message sent successfully")
        return True
        
    except requests.exceptions.HTTPError as http_err:
        error_msg = f"HTTP error occurred: {http_err}"
        if response.status_code == 404:
            error_msg += " (Invalid bot token or chat ID)"
        elif response.status_code == 403:
            error_msg += " (Bot blocked by user or no permissions)"
        log_message(f"❌ {error_msg}")
        
    except requests.exceptions.RequestException as req_err:
        log_message(f"❌ Request failed: {req_err}")
        
    except Exception as e:
        log_message(f"❌ Unexpected error: {e}")
        
    return False


def verify_bot_token():
    """Verify the bot token is valid"""
    if not BOT_TOKEN:
        log_message("❌ No BOT_TOKEN configured")
        return False

    try:
        base_url = f"https://api.telegram.org/bot{BOT_TOKEN}"
        get_me_url = f"{base_url}/getMe"
        
        response = requests.get(get_me_url, timeout=5)
        response.raise_for_status()
        data = response.json()
        
        if data.get('ok'):
            bot_info = data['result']
            log_message(f"🤖 Bot verified: @{bot_info.get('username')} ({bot_info.get('first_name')})")
            return True
            
        log_message(f"❌ Invalid bot token response: {data.get('description')}")
        return False
        
    except Exception as e:
        log_message(f"❌ Bot verification failed: {e}")
        return False


def verify_chat_id():
    """Verify the chat ID is accessible"""
    if not BOT_TOKEN or not CHAT_ID:
        return False

    try:
        base_url = f"https://api.telegram.org/bot{BOT_TOKEN}"
        send_message_url = f"{base_url}/sendMessage"
        
        response = requests.post(
            send_message_url,
            json={"chat_id": CHAT_ID, "text": "🔍 Connection test"},
            timeout=5
        )
        response.raise_for_status()
        
        return True
        
    except requests.exceptions.HTTPError as http_err:
        if response.status_code == 400:
            log_message("❌ Invalid chat ID format")
        elif response.status_code == 403:
            log_message("❌ Bot can't message this chat (need /start first)")
        else:
            log_message(f"❌ Chat verification failed: {http_err}")
        return False
        
    except Exception as e:
        log_message(f"❌ Chat verification error: {e}")
        return False

def check_booking(cinema_id, selected_date):
    url = "https://api3.pvrcinemas.com/api/v1/booking/content/csessions"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": "Bearer",
        "chain": "PVR",
        "city": "Chennai",
        "country": "INDIA",
        "appVersion": "1.0",
        "platform": "WEBSITE",
        "Origin": "https://www.pvrcinemas.com",
        "Referer": "https://www.pvrcinemas.com/",
    }
    payload = {
        "city": "Chennai",
        "cid": cinema_id,
        "lat": "12.883208",
        "lng": "80.3613280",
        "dated": selected_date,
        "qr": "YES",
        "cineType": "",
        "cineTypeQR": ""
    }
    
    retry_count = 3
    for attempt in range(retry_count):
        try:
            start_time = time.time()
            res = requests.post(url, headers=headers, json=payload, timeout=15)
            elapsed_time = (time.time() - start_time) * 1000  # in milliseconds
            
            if res.status_code != 200:
                log_message(f"⚠️ API attempt {attempt + 1} failed with status {res.status_code} for {cinema_id}")
                if attempt < retry_count - 1:
                    time.sleep(2)
                continue
                
            data = res.json()
            log_message(f"✅ API success for {cinema_id} (Response time: {elapsed_time:.2f}ms)")
            return data.get("output", {}).get("cinemaMovieSessions", [])
            
        except requests.exceptions.RequestException as e:
            log_message(f"⚠️ API attempt {attempt + 1} failed with error: {str(e)}")
            if attempt < retry_count - 1:
                time.sleep(2)
            continue
        except json.JSONDecodeError:
            log_message(f"⚠️ API attempt {attempt + 1} failed to decode JSON response")
            if attempt < retry_count - 1:
                time.sleep(2)
            continue
    
    log_message(f"❌ All API attempts failed for {cinema_id}")
    return []

def parse_time_12h(timestr):
    try:
        return datetime.datetime.strptime(timestr, "%I:%M %p").time()
    except ValueError:
        log_message(f"⚠️ Failed to parse time string: {timestr}")
        return None

def is_time_in_range(show_time, from_time, to_time):
    if not all([show_time, from_time, to_time]):
        return True  # If any time is invalid, consider it in range
    
    if from_time <= to_time:
        return from_time <= show_time <= to_time
    else:
        return show_time >= from_time or show_time <= to_time

def monitor_cinema(cinema_name, cinema_id, selected_date, film_name_filter, screen_name_filters, time_from, time_to):
    log_message(f"🔍 Starting monitoring for {cinema_name} on {selected_date}")
    
    while not alert_sent_map.get(cinema_name) and not monitoring_flag.is_set():
        try:
            log_message(f"⏳ Checking {cinema_name}...")
            sessions = check_booking(cinema_id, selected_date)
            found = False
            show_details = []
            
            for session in sessions:
                movie = session.get("movieRe", {})
                film_name = movie.get("filmName", "")
                if film_name_filter and film_name_filter.lower() not in film_name.lower():
                    continue
                
                for exp in session.get("experienceSessions", []):
                    for show in exp.get("shows", []):
                        screen_name = show.get("screenName", "")
                        show_time_str = show.get("showTime", "")
                        subtitle = show.get("subtitle", False)
                        
                        if screen_name_filters and screen_name not in screen_name_filters:
                            continue
                        
                        show_time = parse_time_12h(show_time_str) if show_time_str else None
                        if time_from and time_to and show_time:
                            if not is_time_in_range(show_time, time_from, to_time):
                                continue
                        
                        show_details.append({
                            "movie": film_name,
                            "screen": screen_name,
                            "time": show_time_str,
                            "subtitle": "Yes" if subtitle else "No",
                            "booking_link": f"https://www.pvrcinemas.com/cinemasessions/Chennai/qr/{cinema_id}"
                        })
                        found = True
            
            if found and show_details:
                show_lines = []
                for show in show_details:
                    show_lines.append(
                        f"<b>{show['movie']}</b><br>"
                        f"• Screen: {show['screen']}<br>"
                        f"• Time: {show['time']}<br>"
                        f"• Subtitles: {show['subtitle']}<br>"
                    )
                show_details_msg = "<br>".join(show_lines)
                
                telegram_msg = (
                    f"<b>🎬 Booking is OPEN!</b><br><br>"
                    f"<b>📅 Date:</b> {selected_date}<br>"
                    f"<b>🏢 PVR:</b> {cinema_name}, Chennai<br>"
                )
                
                if film_name_filter:
                    telegram_msg += f"<br><b>🎥 Filtered Film:</b> {film_name_filter}"
                if screen_name_filters:
                    telegram_msg += f"<br><b>📺 Screens:</b> {', '.join(screen_name_filters)}"
                if time_from and time_to:
                    telegram_msg += f"<br><b>⏰ Show Time:</b> {time_from.strftime('%I:%M %p')} - {time_to.strftime('%I:%M %p')}"
                
                telegram_msg += f"<br><br><b>🎭 Matching Shows:</b><br>{show_details_msg}"
                telegram_msg += f"<br><br><a href='https://www.pvrcinemas.com/cinemasessions/Chennai/qr/{cinema_id}'>🎟️ Book Now</a>"
                
                if send_telegram(telegram_msg):
                    alert_sent_map[cinema_name] = True
                    log_message(f"✅ Booking is open for {cinema_name}!")
                    
                    socketio.emit('booking_found', {
                        'cinema': cinema_name,
                        'shows': show_details,
                        'date': selected_date
                    })
                    return
            else:
                log_message(f"🚫 No matching shows at {cinema_name}")
            
            time.sleep(CHECK_INTERVAL)
            
        except Exception as e:
            log_message(f"⚠️ Error in monitoring thread for {cinema_name}: {str(e)}")
            time.sleep(10)  # Wait before retrying after an error

@app.route('/')
def index():
    return render_template('index.html', 
                         cinemas=CINEMA_CODES.keys(),
                         theatre_screens=THEATRE_SCREENS,
                         today=datetime.date.today().strftime("%Y-%m-%d"))

@app.route('/start_monitoring', methods=['POST'])
def start_monitoring():
    global monitoring_flag, monitoring_threads
    
    data = request.json
    selected_cinemas = data.get('cinemas', [])
    selected_date = data.get('date')
    film_name_filter = data.get('film_name', '').strip()
    screen_name_filters = data.get('screens', [])
    time_from_str = data.get('time_from', '').strip()
    time_to_str = data.get('time_to', '').strip()
    
    # Validate inputs
    if not selected_cinemas:
        return jsonify({'error': 'Please select at least one cinema'}), 400
    
    if not selected_date:
        return jsonify({'error': 'Please select a date'}), 400
    
    try:
        datetime.datetime.strptime(selected_date, "%Y-%m-%d")
    except ValueError:
        return jsonify({'error': 'Invalid date format. Use YYYY-MM-DD'}), 400
    
    time_from = time_to = None
    if time_from_str and time_to_str:
        try:
            time_from = parse_time_12h(time_from_str)
            time_to = parse_time_12h(time_to_str)
            if not time_from or not time_to:
                return jsonify({'error': 'Invalid time format. Use format like 04:00 PM'}), 400
        except Exception as e:
            return jsonify({'error': f'Invalid time format: {str(e)}. Use format like 04:00 PM'}), 400
    
    # Clear previous monitoring
    monitoring_flag.clear()
    alert_sent_map.clear()
    monitoring_threads.clear()
    
    log_message(f"✅ Starting monitoring for {', '.join(selected_cinemas)} on {selected_date}")
    
    # Start monitoring threads
    for cinema in selected_cinemas:
        if cinema not in CINEMA_CODES:
            return jsonify({'error': f'Invalid cinema selected: {cinema}'}), 400
        
        cinema_id = CINEMA_CODES[cinema]
        alert_sent_map[cinema] = False
        thread = threading.Thread(
            target=monitor_cinema,
            args=(cinema, cinema_id, selected_date, film_name_filter, screen_name_filters, time_from, time_to),
            daemon=True
        )
        thread.start()
        monitoring_threads.append(thread)
    
    # Send startup notification if Telegram is configured
    if BOT_TOKEN and CHAT_ID:
        telegram_msg = (
            f"<b>🔔 Monitoring Started!</b><br><br>"
            f"<b>🏢 Theatres:</b> {', '.join(selected_cinemas)}<br>"
            f"<b>📅 Date:</b> {selected_date}<br>"
        )
        if film_name_filter:
            telegram_msg += f"<b>🎥 Film Filter:</b> {film_name_filter}<br>"
        if screen_name_filters:
            telegram_msg += f"<b>📺 Screen Filter:</b> {', '.join(screen_name_filters)}<br>"
        if time_from and time_to:
            telegram_msg += f"<b>⏰ Time Range:</b> {time_from.strftime('%I:%M %p')} - {time_to.strftime('%I:%M %p')}<br>"
        telegram_msg += f"<br>Will check every {CHECK_INTERVAL} seconds for open bookings."
        
        send_telegram(telegram_msg)
    
    return jsonify({
        'success': True,
        'message': f'Monitoring started for {len(selected_cinemas)} cinema(s)',
        'check_interval': CHECK_INTERVAL
    })

@app.route('/stop_monitoring', methods=['POST'])
def stop_monitoring():
    global monitoring_flag
    monitoring_flag.set()
    log_message("🛑 Monitoring stopped by user request")
    
    if BOT_TOKEN and CHAT_ID:
        send_telegram("<b>🔕 Monitoring Stopped</b><br><br>The monitoring service has been stopped manually.")
    
    return jsonify({
        'success': True,
        'message': 'Monitoring stopped',
        'active_threads': len(monitoring_threads)
    })

@app.route('/test_telegram', methods=['POST'])
def test_telegram():
    if not BOT_TOKEN or not CHAT_ID:
        return jsonify({
            'success': False,
            'message': 'Telegram not configured. Set BOT_TOKEN and CHAT_ID in environment variables.'
        }), 400
    
    test_msg = (
        "<b>🔔 Test Notification</b><br><br>"
        "✅ Telegram alerts are working properly!<br><br>"
        "You'll receive booking alerts like this when shows become available.<br><br>"
        "<b>PVR Booking Monitor</b> - Ready to go!"
    )
    
    if send_telegram(test_msg):
        log_message("🧪 Test notification sent to Telegram successfully!")
        return jsonify({
            'success': True,
            'message': 'Test notification sent successfully'
        })
    else:
        return jsonify({
            'success': False,
            'message': 'Failed to send test notification'
        }), 500

@app.route('/get_logs')
def get_logs():
    return jsonify({
        'success': True,
        'logs': logs,
        'count': len(logs)
    })

@app.route('/clear_logs', methods=['POST'])
def clear_logs():
    global logs
    logs.clear()
    log_message("🗑️ Logs cleared by user request")
    return jsonify({
        'success': True,
        'message': 'Logs cleared',
        'remaining_logs': len(logs)
    })

@app.route('/get_screens/<cinema>')
def get_screens(cinema):
    if cinema not in THEATRE_SCREENS:
        return jsonify({'success': False, 'error': 'Invalid cinema name'}), 404
    
    return jsonify({
        'success': True,
        'cinema': cinema,
        'screens': THEATRE_SCREENS[cinema]
    })

@app.route('/status')
def status():
    return jsonify({
        'status': 'running',
        'telegram_configured': bool(BOT_TOKEN and CHAT_ID),
        'active_monitoring': not monitoring_flag.is_set(),
        'monitoring_threads': len(monitoring_threads),
        'last_logs_count': len(logs),
        'check_interval': CHECK_INTERVAL
    })

@socketio.on('connect')
def handle_connect():
    log_message("🔗 New client connected via WebSocket")
    emit('initial_logs', {'logs': logs})

@socketio.on('disconnect')
def handle_disconnect():
    log_message("🔌 Client disconnected from WebSocket")

if __name__ == '__main__':
    log_message("🚀 PVR Booking Monitor Web App started!")
    log_message(f"Telegram configured: {'✅' if BOT_TOKEN and CHAT_ID else '❌'}")
    if BOT_TOKEN and CHAT_ID:
        log_message("Testing Telegram connection...")
        if send_telegram("<b>🔔 PVR Monitor Startup</b><br><br>Service has started successfully!"):
            log_message("✅ Telegram connection test successful")
        else:
            log_message("❌ Telegram connection test failed")
    
    # For local testing only; Render uses gunicorn
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False)
