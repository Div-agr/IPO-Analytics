import os
import socket
import pickle
import pandas as pd
from flask import Flask, request, jsonify
from flask_mail import Mail, Message
from flask_cors import CORS
from datetime import datetime
from dotenv import load_dotenv

import scraper
import store  # ← Postgres-backed (Neon) notification dedup + manual overrides

load_dotenv()
socket.setdefaulttimeout(15)

_orig_getaddrinfo = socket.getaddrinfo
def _ipv4_only_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
socket.getaddrinfo = _ipv4_only_getaddrinfo
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

# Fail loudly if this isn't set — a hardcoded fallback here would mean
# anyone who's seen this source code could trigger the cron endpoint
# without knowing the real secret.
CRON_SECRET = os.getenv("CRON_SECRET")
if not CRON_SECRET:
    raise RuntimeError("CRON_SECRET environment variable is not set")

mail = Mail(app)

# Load model and scaler from pickle files
with open('ipo_model.pkl', 'rb') as model_file:
    model = pickle.load(model_file)

with open('scaler.pkl', 'rb') as scaler_file:
    scaler = pickle.load(scaler_file)

# Subscribed users live in the database (store.py) — see /api/subscribers below.


# ── Email notifications ────────────────────────────────────────────────────────

def send_email_notification(new_ipo):
    recipients = store.get_subscribers()
    if not recipients:
        print("No subscribers found. Skipping email dispatch.")
        return

    ipo_name = new_ipo.get('IPO', 'Unknown IPO')
    apply_date = new_ipo.get('Apply Date', 'N/A')
    ipo_price = new_ipo.get('IPO Price', 'N/A')
    gmp = new_ipo.get('GMP', 'N/A')
    gmp_ratio = new_ipo.get('GMP_to_IPO_Ratio', 0.0)
    subscription = new_ipo.get('Subscription', 'N/A')
    prob = new_ipo.get('Apply_Probability', 0.0)

    price_str = f"₹{ipo_price}" if isinstance(ipo_price, (int, float)) else str(ipo_price)
    gmp_str = f"₹{gmp}" if isinstance(gmp, (int, float)) else str(gmp)
    gmp_ratio_str = f"{gmp_ratio * 100:.1f}%" if isinstance(gmp_ratio, (int, float)) else "N/A"
    sub_str = f"{subscription}x" if isinstance(subscription, (int, float)) else str(subscription)
    prob_str = f"{prob:.2%}" if isinstance(prob, (int, float)) else "N/A"

    subject = f" New IPO Alert: {ipo_name}"

    html_content = f"""
    <div style="font-family: Arial, sans-serif; max-width: 500px; padding: 20px; border: 1px solid #e0e0e0; border-radius: 8px;">
        <h2 style="color: #1a73e8; margin-top: 0;">{ipo_name}</h2>
        <p style="color: #555;">A new IPO has opened for subscription this month.</p>

        <table style="width: 100%; border-collapse: collapse; margin-top: 15px;">
            <tr style="border-bottom: 1px solid #eee;">
                <td style="padding: 8px 0; color: #666;">Apply Date</td>
                <td style="padding: 8px 0; font-weight: bold; text-align: right;">{apply_date}</td>
            </tr>
            <tr style="border-bottom: 1px solid #eee;">
                <td style="padding: 8px 0; color: #666;">Issue Price</td>
                <td style="padding: 8px 0; font-weight: bold; text-align: right;">{price_str}</td>
            </tr>
            <tr style="border-bottom: 1px solid #eee;">
                <td style="padding: 8px 0; color: #666;">Current GMP</td>
                <td style="padding: 8px 0; font-weight: bold; text-align: right; color: #2e7d32;">{gmp_str} ({gmp_ratio_str})</td>
            </tr>
            <tr style="border-bottom: 1px solid #eee;">
                <td style="padding: 8px 0; color: #666;">Subscription</td>
                <td style="padding: 8px 0; font-weight: bold; text-align: right;">{sub_str}</td>
            </tr>
            <tr>
                <td style="padding: 8px 0; color: #666;">Success Probability</td>
                <td style="padding: 8px 0; font-weight: bold; text-align: right; color: #1565c0; font-size: 16px;">{prob_str}</td>
            </tr>
        </table>
    </div>
    """

    with app.app_context():
        with mail.connect() as conn:
            for user_email in recipients:
                msg = Message(
                    subject=subject,
                    sender=app.config['MAIL_USERNAME'],
                    recipients=[user_email],
                    body=f"IPO Alert for {ipo_name}. Date: {apply_date}, Probability: {prob_str}",
                    html=html_content
                )
                conn.send(msg)
                print(f"Dispatched HTML email to: {user_email}")


def send_notifications_for_current_month():
    """
    Send email alerts for IPOs opening TODAY — dedup tracked in store.py
    (Neon Postgres) so it survives restarts/redeploys and works across
    the stateless cron-triggered invocations this runs under.
    """
    today = datetime.now().date()
    ipos = scraper.get_ipo_data()

    sent_count = 0
    for ipo in ipos:
        try:
            apply_date_str = ipo.get("Apply Date", "")
            if not apply_date_str:
                continue
            ipo_date = datetime.strptime(apply_date_str, '%Y-%m-%d').date()
            if ipo_date != today:
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
    # No in-process background thread anymore — notifications are triggered
    # externally via /api/cron/trigger-notifications, which is more reliable
    # on hosts (like Render's free tier) that spin the process down when idle.
    with app.app_context():
        predict()
    app.run(debug=False)