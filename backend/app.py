import os
import pickle
import pandas as pd
from flask import Flask, request, jsonify
from flask_mail import Mail, Message
from flask_cors import CORS
from datetime import datetime
import time
import threading
from dotenv import load_dotenv

import scraper  # ← live data from investorgain.com, cached in Upstash Redis
import store    # ← Postgres-backed (Neon) notification dedup + manual overrides

load_dotenv()

# Initialize Flask app
app = Flask(__name__)
CORS(app)

# Flask-Mail Configuration for Gmail
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 465
app.config['MAIL_USE_TLS'] = False
app.config['MAIL_USE_SSL'] = True
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')
CRON_SECRET = os.getenv("CRON_SECRET", "your-fallback-secret-key")


mail = Mail(app)

# Load model and scaler from pickle files
with open('ipo_model.pkl', 'rb') as model_file:
    model = pickle.load(model_file)

with open('scaler.pkl', 'rb') as scaler_file:
    scaler = pickle.load(scaler_file)

# Subscribed users now live in the database (store.py) — see /api/subscribers
# below. No more editing an env var to add/remove someone.


# ── Email notifications ────────────────────────────────────────────────────────

def send_email_notification(new_ipo):
    recipients = store.get_subscribers()
    if not recipients:
        print("No subscribers found. Skipping email dispatch.")
        return

    subject = f"New IPO Alert: {new_ipo.get('IPO', 'Unknown')}"
    body = (
        f"A new IPO has been listed:\n\n"
        f"IPO: {new_ipo.get('IPO', 'N/A')}\n"
        f"Date: {new_ipo.get('Apply Date', 'N/A')}\n"
        f"Success Probability: {new_ipo.get('Apply_Probability', 0.0):.2%}\n\n"
        f"Don't miss out on this opportunity!"
    )

    with app.app_context():
        # Open one single SMTP connection for all recipients
        with mail.connect() as conn:
            for user_email in recipients:
                msg = Message(
                    subject=subject,
                    sender=app.config['MAIL_USERNAME'],
                    recipients=[user_email],
                    body=body
                )
                conn.send(msg)
                print(f"Dispatched email to: {user_email}")


def send_notifications_for_current_month():
    """
    Send email alerts for IPOs opening in the current month — but only
    once per (IPO, Apply Date), tracked in store.py (Neon Postgres) so
    the dedup state survives restarts/redeploys.
    """
    current_month = datetime.now().month
    current_year = datetime.now().year
    ipos = scraper.get_ipo_data()

    sent_count = 0
    for ipo in ipos:
        try:
            apply_date_str = ipo.get("Apply Date", "")
            if not apply_date_str:
                continue
            ipo_date = datetime.strptime(apply_date_str, '%Y-%m-%d')
            if ipo_date.month != current_month or ipo_date.year != current_year:
                continue

            ipo_name = ipo.get("IPO", "")
            if not ipo_name or store.is_notified(ipo_name, apply_date_str):
                continue

            send_email_notification(ipo)
            store.mark_notified(ipo_name, apply_date_str)
            sent_count += 1
            print(f"Notification sent for IPO: {ipo_name} on {apply_date_str}")

        except Exception as e:
            print(f"Error processing IPO for notification: {e}")

    if sent_count == 0:
        print("No new IPO notifications to send.")


def schedule_daily_notifications():
    """Run email notification loop in background thread (every 10 minutes)."""
    def notification_loop():
        with app.app_context():
            while True:
                try:
                    send_notifications_for_current_month()
                except Exception as e:
                    print(f"Notification loop error: {e}")
                time.sleep(600)  # 10 minutes
    threading.Thread(target=notification_loop, daemon=True).start()


@app.route('/api/cron/trigger-notifications', methods=['GET', 'POST'])
def trigger_cron_notifications():
    """Endpoint triggered by an external cron service every 12 hours."""
    token = request.headers.get('X-Cron-Secret') or request.args.get('secret')
    if token != CRON_SECRET:
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        # 1. Run ML predictions and refresh cache
        predict()
        
        # 2. Send emails for current month's IPOs
        send_notifications_for_current_month()
        
        return jsonify({
            'status': 'success',
            'message': 'Data scraped, predictions updated, and emails sent.'
        }), 200

    except Exception as e:
        print(f"Cron execution error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/health', methods=['GET'])
def health_check():
    return jsonify({'status': 'healthy'}), 200

# ── Flask Routes ───────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return "IPO Prediction Backend is up and running!"


@app.route('/predict', methods=['GET'])
def predict():
    """
    Fetch fresh IPO data from investorgain.com, run the ML model to compute
    Apply_Probability for each IPO, cache the ranked results, and return them.
    """
    try:
        raw_ipos = scraper.get_raw_ipo_data()
        if not raw_ipos:
            scraper.get_ipo_data(force_refresh=True)
            raw_ipos = scraper.get_raw_ipo_data()

        if not raw_ipos:
            return jsonify({"error": "Failed to fetch IPO data from investorgain.com"}), 503

        upcoming_data = pd.DataFrame(raw_ipos)

        # Only rows with a real price and the features the model needs are
        # usable for prediction. Rows without a finalized price band are
        # still shown on the calendar (via scraper.get_ipo_data) — they're
        # just excluded here.
        required = ['IPO Price', 'Subscription', 'GMP', 'GMP_to_IPO_Ratio']
        upcoming_data = upcoming_data.dropna(subset=required)
        upcoming_data = upcoming_data[upcoming_data['IPO Price'] > 0]

        if upcoming_data.empty:
            return jsonify({"error": "No valid IPO rows available for prediction"}), 503

        X = upcoming_data[['Subscription', 'GMP', 'IPO Price', 'GMP_to_IPO_Ratio']]
        X_scaled = scaler.transform(X)
        upcoming_data = upcoming_data.copy()
        upcoming_data['Apply_Probability'] = model.predict_proba(X_scaled)[:, 1]

        ranked = (
            upcoming_data[['IPO', 'Apply Date', 'Apply_Probability']]
            .sort_values(by='Apply_Probability', ascending=False)
        )
        ranked['Apply Date'] = ranked['Apply Date'].astype(str)
        ranked_list = ranked.to_dict(orient='records')

        # Cache ranked results so /api/ipo_data can serve probabilities
        scraper.update_ranked_ipos(ranked_list)

        return jsonify({
            "message": "IPO predictions computed successfully.",
            "ranked_ipos": ranked_list
        })

    except Exception as e:
        print(f"Error in /predict: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/ipo_data', methods=['GET'])
def get_ipo_data():
    """
    Return IPO data (with Apply_Probability if /predict has been called),
    optionally filtered by a specific apply date.
    """
    ipos = scraper.get_ipo_data()
    date = request.args.get('date')

    if date:
        selected = [ipo for ipo in ipos if ipo.get("Apply Date") == date]
        return jsonify({'date': date, 'ipos': selected})
    else:
        grouped = {}
        for ipo in ipos:
            date_key = ipo.get("Apply Date", "")
            if not date_key:
                continue
            grouped.setdefault(date_key, []).append(ipo)
        return jsonify(grouped)


@app.route('/api/ipo_data_range', methods=['GET'])
def get_ipo_data_range():
    """Return IPOs whose Apply Date falls within [start, end] (YYYY-MM-DD)."""
    start_date = request.args.get('start')
    end_date = request.args.get('end')

    if not start_date or not end_date:
        return jsonify({'error': 'start and end date parameters are required'}), 400

    try:
        start = datetime.strptime(start_date, '%Y-%m-%d').date()
        end = datetime.strptime(end_date, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'error': 'Invalid date format — use YYYY-MM-DD'}), 400

    ipos = scraper.get_ipo_data()
    filtered = []
    for ipo in ipos:
        try:
            apply_str = ipo.get("Apply Date", "")
            if not apply_str:
                continue
            ipo_date = datetime.strptime(apply_str, '%Y-%m-%d').date()
            if start <= ipo_date <= end:
                filtered.append(ipo)
        except Exception as parse_err:
            print(f"Error parsing IPO date: {parse_err}")

    return jsonify({'start': start_date, 'end': end_date, 'ipos': filtered})


@app.route('/api/add_ipo', methods=['POST'])
def add_ipo():
    """
    Manually add or override an IPO entry. Replaces the old SheetDB POST —
    entries are persisted in Neon Postgres (store.py) and merged into
    scraper.get_ipo_data() output, taking precedence over scraped data
    for the same (IPO, Apply Date) key.

    Expected JSON body, e.g.:
        {"IPO": "Example Ltd", "Apply Date": "2026-09-01", "IPO Price": 250, ...}
    """
    payload = request.get_json(silent=True) or {}
    try:
        store.add_manual_ipo(payload)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    return jsonify({"message": "IPO added.", "ipo": payload}), 201


@app.route('/api/add_ipo', methods=['DELETE'])
def delete_manual_ipo():
    """Remove a manually-added IPO override by IPO name + Apply Date."""
    ipo_name = request.args.get('ipo')
    apply_date = request.args.get('date')
    if not ipo_name or not apply_date:
        return jsonify({'error': "'ipo' and 'date' query params are required"}), 400

    removed = store.remove_manual_ipo(ipo_name, apply_date)
    if not removed:
        return jsonify({'error': 'No matching manual IPO entry found'}), 404
    return jsonify({'message': 'Manual IPO entry removed.'})


@app.route('/api/subscribers', methods=['GET'])
def list_subscribers():
    """List currently subscribed emails."""
    return jsonify({'subscribers': store.get_subscribers()})


@app.route('/api/subscribers', methods=['POST'])
def add_subscriber_route():
    """
    Add an email to the notification list.
    Expected JSON body: {"email": "someone@example.com"}
    """
    payload = request.get_json(silent=True) or {}
    email = payload.get('email', '')
    try:
        store.add_subscriber(email)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    return jsonify({'message': 'Subscribed.', 'email': email.strip().lower()}), 201


@app.route('/api/subscribers', methods=['DELETE'])
def remove_subscriber_route():
    """Remove an email from the notification list via ?email=... query param."""
    email = request.args.get('email', '')
    if not email:
        return jsonify({'error': "'email' query param is required"}), 400
    removed = store.remove_subscriber(email)
    if not removed:
        return jsonify({'error': 'No matching subscriber found'}), 404
    return jsonify({'message': 'Unsubscribed.'})


@app.route('/api/refresh', methods=['POST'])
def refresh_data():
    """Force a fresh fetch from investorgain.com and re-run predictions."""
    try:
        scraper.get_ipo_data(force_refresh=True)
        with app.app_context():
            return predict()
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Entrypoint ─────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    with app.app_context():
        predict()
    app.run(debug=True, use_reloader=False)