const cron = require('node-cron');
const { fetchIPOs } = require('../services/sheetdb');
const { sendIPOEmail } = require('../services/mailer');
const { getSubscribers } = require('../services/subscriberService');
const pool = require('../db'); 

async function hasNotificationBeenSent(email, ipoName) {
  const result = await pool.query(
    'SELECT 1 FROM sent_notifications WHERE email = $1 AND ipo_name = $2',
    [email, ipoName]
  );
  return result.rowCount > 0;
}

async function markNotificationAsSent(email, ipoName) {
  try {
    await pool.query(
      'INSERT INTO sent_notifications (email, ipo_name) VALUES ($1, $2) ON CONFLICT DO NOTHING',
      [email, ipoName]
    );
  } catch (err) {
    console.error('Error recording sent notification:', err);
  }
}

function scheduleNotifications() {
  cron.schedule('0 0 * * *', async () => {
    //console.log('⏰ Running IPO notification job every 24 hours..');

    try {
      const ipos = await fetchIPOs();
      const subscribers = await getSubscribers();

      for (const ipo of ipos) {
        const ipoName = ipo.IPO;

        for (const email of subscribers) {
          const alreadySent = await hasNotificationBeenSent(email, ipoName);
          if (!alreadySent) {
            await sendIPOEmail(ipo, [email]);
            await markNotificationAsSent(email, ipoName);
            //console.log(`✅ Sent IPO "${ipoName}" to ${email}`);
          } else {
            //console.log(`⏩ Skipped duplicate IPO "${ipoName}" for ${email}`);
          }
        }
      }
    } catch (err) {
      console.error('❌ Notification job failed:', err);
    }
  });
}

module.exports = scheduleNotifications;
