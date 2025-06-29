const pool = require('../db');

async function getSubscribers() {
  const result = await pool.query('SELECT email FROM subscribers');
  return result.rows.map((row) => row.email);
}

module.exports = { getSubscribers };
