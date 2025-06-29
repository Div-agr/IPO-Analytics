const express = require('express');
const {
  predict,
  getIPOData,
  getIPODataRange,
  addIPO,
  addSubscriber
} = require('../controllers/ipoController');

const router = express.Router();

router.get('/predict', predict);
router.get('/ipo_data', getIPOData);
router.get('/ipo_data_range', getIPODataRange);
router.post('/add_ipo', addIPO);
router.post('/subscribe', addSubscriber);

module.exports = router;
