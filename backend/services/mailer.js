const nodemailer = require('nodemailer');

console.log('MAIL_USERNAME:', process.env.MAIL_USERNAME);
const transporter = nodemailer.createTransport({
  service: 'gmail',
  auth: {
    user: process.env.MAIL_USERNAME,
    pass: process.env.MAIL_PASSWORD,
  },
});

function sendIPOEmail(ipo, recipients) {
  const subject = `New IPO Alert: ${ipo.IPO}`;
  const text = `
A new IPO has been listed:

IPO: ${ipo.IPO}
Date: ${ipo['Apply Date']}
Success Probability: ${ipo.Apply_Probability}

Don't miss out on this opportunity!
  `;

  recipients.forEach((email) => {
    transporter.sendMail({
      from: process.env.MAIL_USERNAME,
      to: email,
      subject,
      text,
    }, (err, info) => {
      if (err) console.error(`Email error to ${email}:`, err);
      else console.log(`Email sent to ${email}:`, info.response);
    });
  });
}

module.exports = { sendIPOEmail };
