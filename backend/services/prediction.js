const axios = require('axios');

// This assumes the Python model is hosted on localhost:5001 and expects raw IPO data
async function getPredictions(rawIPOData) {
  const response = await axios.post('http://localhost:5001/predict', { data: rawIPOData });
  return response.data; // should return IPOs with Apply_Probability added
}

module.exports = { getPredictions };
