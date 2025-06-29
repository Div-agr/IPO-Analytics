const { fetchIPOs, postIPOs } = require('../services/sheetdb');
const { sendIPOEmail } = require('../services/mailer');
const { getPredictions } = require('../services/prediction');
const { getSubscribers } = require('../services/subscriberService');
const pool = require('../db');


const predict = async (req, res) => {
  try {
    const ipos = await fetchIPOs();
    const predictions = await getPredictions(ipos); // assumes Flask microservice handles data cleaning & predicting
    await postIPOs(predictions);
    res.json({ message: 'Ranked IPOs saved successfully!' });
  } catch (err) {
    console.error(err);
    res.status(500).json({ error: 'Prediction failed' });
  }
};

const getIPOData = async (req, res) => {
  try {
    const date = req.query.date;
    const ipos = await fetchIPOs();

    if (date) {
      const filtered = ipos.filter((ipo) => ipo['Apply Date'] === date);
      return res.json({ date, ipos: filtered });
    }

    const grouped = {};
    for (const ipo of ipos) {
      const key = ipo['Apply Date'];
      if (!grouped[key]) grouped[key] = [];
      grouped[key].push(ipo);
    }

    res.json(grouped);
  } catch (err) {
    console.error(err);
    res.status(500).json({ error: 'Failed to fetch IPOs' });
  }
};

const getIPODataRange = async (req, res) => {
  const { start, end } = req.query;
  if (!start || !end) return res.status(400).json({ error: 'Start and end required' });

  try {
    const ipos = await fetchIPOs();
    const sDate = new Date(start);
    const eDate = new Date(end);

    const filtered = ipos.filter((ipo) => {
      const d = new Date(ipo['Apply Date']);
      return d >= sDate && d <= eDate;
    });

    res.json({ start, end, ipos: filtered });
  } catch (err) {
    console.error(err);
    res.status(500).json({ error: 'Failed to filter IPOs' });
  }
};

const addIPO = async (req, res) => {
  const newIPO = req.body;
  try {
    await postIPOs([newIPO]);
    const subscribers = await getSubscribers();
    sendIPOEmail(newIPO, subscribers);
    res.status(201).json({ message: 'IPO added and email sent' });
  } catch (err) {
    console.error(err);
    res.status(500).json({ error: 'Failed to add IPO' });
  }
};

// routes/ipoRoutes.js or wherever your subscription handler is
const addSubscriber=  async (req, res) => {
  const { email } = req.body;

  try {
    await pool.query('INSERT INTO subscribers (email) VALUES ($1)', [email]);
    res.status(200).json({ message: 'Subscribed successfully' });
  } catch (err) {
    if (err.code === '23505') {
      // 23505 = unique_violation in PostgreSQL
      return res.status(409).json({ message: 'Email already subscribed' });
    }
    console.error(err);
    res.status(500).json({ message: 'Server error' });
  }
};


module.exports = {
  predict,
  getIPOData,
  getIPODataRange,
  addIPO,
  addSubscriber
};
