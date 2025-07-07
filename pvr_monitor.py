from flask import Flask, render_template, request, jsonify, redirect, url_for
import requests
import time
import os
import datetime
import threading
import json
import logging
from flask_socketio import SocketIO, emit
import calendar

# === Logging Setup ===
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

# === Flask App Setup ===
app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'your-secret-key-here')
socketio = SocketIO(app, cors_allowed_origins="*")

# === Telegram Bot Setup ===
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

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
    # Send to all connected clients
    socketio.emit('log_update', {'message': log_entry})

def send_telegram(msg):
    try:
        if not BOT_TOKEN or not CHAT_ID:
            log_message("❌ Telegram credentials not configured")
            return
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        res = requests.post(url, data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=10)
        if res.status_code == 200:
            log_message("📲 Telegram alert sent!")
        else:
            log_message(f"❌ Telegram failed: {res.text}")
    except Exception as e:
        log_message(f"❌ Telegram error: {e}")

def check_booking(cinema_id, selected_date):
    url = "https://api3.pvrcinemas.com/api/v1/booking/content/csessions"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": "Bearer",
        "chain": "PVR",
        "city": "Chennai",
        "country": "INDIA",
        "appVersion": "1.0",
        "platform": "WEBSITE",
        "Origin": "https://www.pvrcinemas.com",
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
    for _ in range(3):
        try:
            res = requests.post(url, headers=headers, json=payload, timeout=10)
            data = res.json()
            return data.get("output", {}).get("cinemaMovieSessions", [])
        except Exception as e:
            log_message(f"Retry due to API error: {e}")
            time.sleep(2)
    return []

def parse_time_12h(timestr):
    return datetime.datetime.strptime(timestr, "%I:%M %p").time()

def is_time_in_range(show_time, from_time, to_time):
    if from_time <= to_time:
        return from_time <= show_time <= to_time
    else:
        return show_time >= from_time or show_time <= to_time

def monitor_cinema(cinema_name, cinema_id, selected_date, film_name_filter, screen_name_filters, time_from, time_to):
    while not alert_sent_map.get(cinema_name) and not monitoring_flag.is_set():
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
                    
                    if time_from and time_to and show_time_str:
                        try:
                            show_time = parse_time_12h(show_time_str)
                            if not is_time_in_range(show_time, time_from, time_to):
                                continue
                        except Exception:
                            continue
                    
                    show_details.append({
                        "movie": film_name,
                        "screen": screen_name,
                        "time": show_time_str,
                        "subtitle": "Yes" if subtitle else "No"
                    })
                    found = True
        
        if found and show_details:
            show_lines = []
            for show in show_details:
                show_lines.append(
                    f"🎬 *{show['movie']}*\n"
                    f"• 🎥 Screen: {show['screen']}\n"
                    f"• 🕒 Time: {show['time']}\n"
                    f"• 💬 Subtitles: {show['subtitle']}\n"
                )
            show_details_msg = "\n".join(show_lines)
            telegram_msg = (
                f"🎉 *Booking is OPEN!* 🎟️\n\n"
                f"🗓️ *{selected_date}*\n"
                f"📍 *PVR: {cinema_name}, Chennai*\n"
            )
            if film_name_filter:
                telegram_msg += f"\n🎬 *Filtered Film:* {film_name_filter}"
            if screen_name_filters:
                telegram_msg += f"\n *Screens:* {', '.join(screen_name_filters)}"
            if time_from and time_to:
                telegram_msg += f"\n🕒 *Show Time:* {time_from.strftime('%I:%M %p')} - {time_to.strftime('%I:%M %p')}"
            telegram_msg += f"\n\n*Matching Shows:*\n{show_details_msg}"
            telegram_msg += f"\n🔗 [Book Now](https://www.pvrcinemas.com/cinemasessions/Chennai/qr/{cinema_id})"
            
            send_telegram(telegram_msg)
            alert_sent_map[cinema_name] = True
            log_message(f"✅ Booking is open for {cinema_name}!")
            
            # Send booking found event to all clients
            socketio.emit('booking_found', {
                'cinema': cinema_name,
                'shows': show_details,
                'date': selected_date
            })
            return
        else:
            log_message(f"🚫 No matching shows at {cinema_name}.")
        
        time.sleep(CHECK_INTERVAL)

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
        return jsonify({'error': 'Invalid date format'}), 400
    
    time_from = time_to = None
    if time_from_str and time_to_str:
        try:
            time_from = parse_time_12h(time_from_str)
            time_to = parse_time_12h(time_to_str)
        except Exception:
            return jsonify({'error': 'Invalid time format. Use format like 04:00 PM'}), 400
    
    # Clear previous monitoring
    monitoring_flag.clear()
    alert_sent_map.clear()
    monitoring_threads.clear()
    
    log_message(f"✅ Monitoring {', '.join(selected_cinemas)} on {selected_date} every {CHECK_INTERVAL} seconds...")
    send_telegram(f"🔍 *Monitoring started!*\n\n🎬 *Theatres:* {', '.join(selected_cinemas)}\n📅 *Date:* {selected_date}")
    
    # Start monitoring threads
    for cinema in selected_cinemas:
        cinema_id = CINEMA_CODES[cinema]
        alert_sent_map[cinema] = False
        thread = threading.Thread(
            target=monitor_cinema,
            args=(cinema, cinema_id, selected_date, film_name_filter, screen_name_filters, time_from, time_to),
            daemon=True
        )
        thread.start()
        monitoring_threads.append(thread)
    
    return jsonify({'success': True, 'message': 'Monitoring started successfully'})

@app.route('/stop_monitoring', methods=['POST'])
def stop_monitoring():
    global monitoring_flag
    monitoring_flag.set()
    log_message("🛑 Monitoring stopped.")
    send_telegram("🛑 *Monitoring stopped manually.*")
    return jsonify({'success': True, 'message': 'Monitoring stopped'})

@app.route('/test_telegram', methods=['POST'])
def test_telegram():
    test_msg = "🧪 *Test Notification* 🧪\n\n✅ Telegram alerts are working!\n\nYou'll receive booking alerts like this when shows become available.\n\n🎬 *PVR Booking Monitor* - Ready to go!"
    send_telegram(test_msg)
    log_message("🧪 Test notification sent to Telegram!")
    return jsonify({'success': True, 'message': 'Test notification sent'})

@app.route('/get_logs')
def get_logs():
    return jsonify({'logs': logs})

@app.route('/clear_logs', methods=['POST'])
def clear_logs():
    global logs
    logs.clear()
    log_message("🗑️ Logs cleared")
    return jsonify({'success': True, 'message': 'Logs cleared'})

@app.route('/get_screens/<cinema>')
def get_screens(cinema):
    screens = THEATRE_SCREENS.get(cinema, [])
    return jsonify({'screens': screens})

@socketio.on('connect')
def handle_connect():
    log_message(f"🔗 Client connected")
    # Send current logs to new client
    emit('initial_logs', {'logs': logs})

@socketio.on('disconnect')
def handle_disconnect():
    log_message(f"🔌 Client disconnected")

if __name__ == '__main__':
    log_message("🚀 PVR Booking Monitor Web App started!")
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=False, allow_unsafe_werkzeug=True)

