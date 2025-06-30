const express = require('express');
const cors = require('cors');
require('dotenv').config({ path: __dirname + '/.env' });
const ipoRoutes = require('./routes/ipoRoutes');
const scheduleNotifications = require('./jobs/notificationJob');

const app = express();

const allowedOrigins = ['https://ipo-analytics.onrender.com'];
app.use(cors({
  origin: function (origin, callback) {
    if (!origin || allowedOrigins.includes(origin)) {
      callback(null, true);
    } else {
      callback(new Error('CORS not allowed for this origin'));
    }
  }
}));
app.use(express.json());

app.use('/api', ipoRoutes);

app.get('/', (req, res) => res.send('IPO Backend Running in Node.js'));

scheduleNotifications();

const PORT = process.env.PORT || 5000;
app.listen(PORT, () => console.log(`Server running on port ${PORT}`));
