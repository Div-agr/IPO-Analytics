const axios = require('axios');

const SHEETDB_POST_URL = 'https://sheetdb.io/api/v1/dbc7f5wv0p6rp';
const SHEETDB_FETCH_URL = 'https://sheetdb.io/api/v1/y5gs1s5pan0fi';

async function fetchIPOs() {
  const response = await axios.get(SHEETDB_FETCH_URL);
  return response.data;
}

async function postIPOs(data) {
  const response = await axios.post(SHEETDB_POST_URL, { data });
  return response.data;
}

module.exports = {
  fetchIPOs,
  postIPOs,
};
