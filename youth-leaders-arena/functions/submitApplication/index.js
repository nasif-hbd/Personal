const { Firestore } = require('@google-cloud/firestore');
const sgMail = require('@sendgrid/mail');

const firestore = new Firestore();
sgMail.setApiKey(process.env.SENDGRID_API_KEY);

const FROM_EMAIL = process.env.FROM_EMAIL;
// Comma-separated list, e.g. "https://ylarena.online,https://www.ylarena.online"
const ALLOWED_ORIGINS = (process.env.ALLOWED_ORIGIN || '*')
  .split(',')
  .map((o) => o.trim());
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function setCors(req, res) {
  const origin = req.get('Origin');
  const allowed = ALLOWED_ORIGINS.includes('*')
    ? '*'
    : ALLOWED_ORIGINS.includes(origin) ? origin : ALLOWED_ORIGINS[0];
  res.set('Access-Control-Allow-Origin', allowed);
  res.set('Access-Control-Allow-Methods', 'POST, OPTIONS');
  res.set('Access-Control-Allow-Headers', 'Content-Type');
}

function confirmationEmail(name, program) {
  const programLine = program ? ` for <strong>${program}</strong>` : '';
  return {
    subject: 'Application received — Youth Leaders Arena',
    html: `
      <p>Hi ${name},</p>
      <p>Thanks for applying to Youth Leaders Arena${programLine}. We've received your application and our team will follow up soon.</p>
      <p>— Youth Leaders Arena</p>
    `,
  };
}

exports.submitApplication = async (req, res) => {
  setCors(req, res);

  if (req.method === 'OPTIONS') {
    res.status(204).send('');
    return;
  }
  if (req.method !== 'POST') {
    res.status(405).json({ error: 'Method not allowed' });
    return;
  }

  const body = req.body || {};
  const name = String(body.name || '').trim();
  const age = String(body.age || '').trim();
  const email = String(body.email || '').trim();
  const phone = String(body.phone || '').trim();
  const program = String(body.program || '').trim();
  const reason = String(body.reason || '').trim();

  if (!name || !email || !EMAIL_RE.test(email)) {
    res.status(400).json({ error: 'A valid name and email are required.' });
    return;
  }

  try {
    await firestore.collection('applications').add({
      name,
      age,
      email,
      phone,
      program,
      reason,
      submittedAt: Firestore.FieldValue.serverTimestamp(),
    });

    const { subject, html } = confirmationEmail(name, program);
    await sgMail.send({ to: email, from: FROM_EMAIL, subject, html });

    res.status(200).json({ ok: true });
  } catch (err) {
    console.error('submitApplication failed:', err);
    res.status(500).json({ error: 'Something went wrong. Please try again later.' });
  }
};
