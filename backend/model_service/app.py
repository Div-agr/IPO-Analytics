# backend/model_service/app.py
import pickle
import pandas as pd
from flask import Flask, request, jsonify
from flask_cors import CORS
from sklearn.preprocessing import StandardScaler

app = Flask(__name__)
CORS(app)

# Load model and scaler
with open('ipo_model.pkl', 'rb') as f:
    model = pickle.load(f)

with open('scaler.pkl', 'rb') as f:
    scaler = pickle.load(f)

@app.route('/predict', methods=['POST'])
def predict():
    try:
        data = request.json['data']
        df = pd.DataFrame(data)

        # Preprocessing
        df['IPO_Size'] = df['IPO_Size'].astype(str).str.replace(r'[₹,]', '', regex=True)
        df['IPO_Size'] = df['IPO_Size'].str.replace(r'[Cc]r', '', regex=True).str.strip()
        df['IPO_Size'] = df['IPO_Size'].astype(float) * 1e7

        df['IPO Price'] = df['IPO Price'].astype(str).str.replace(r'[^0-9.]', '', regex=True).astype(float)
        df['Subscription'] = df['Subscription'].astype(str).str.replace('x', '', regex=False).astype(float)
        df['GMP'] = df['GMP'].astype(str).str.replace(r'[₹]', '', regex=True).astype(float)

        df['GMP_to_IPO_Ratio'] = df['GMP'] / df['IPO Price']
        df = df.dropna(subset=['IPO Price', 'Subscription', 'GMP', 'GMP_to_IPO_Ratio'])

        X = df[['Subscription', 'GMP', 'IPO Price', 'GMP_to_IPO_Ratio']]
        X_scaled = scaler.transform(X)

        df['Apply_Probability'] = model.predict_proba(X_scaled)[:, 1]
        result = df[['IPO', 'Apply Date', 'Apply_Probability']]
        result['Apply Date'] = result['Apply Date'].astype(str)

        return jsonify(result.to_dict(orient='records'))

    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(port=5001, debug=True)
