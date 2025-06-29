// backend/db.js
const { Pool } = require('pg');
require('dotenv').config();

const pool = new Pool({
  connectionString: process.env.POSTGRES_URL, // or use individual fields like host/user/pass
});

module.exports = pool;
