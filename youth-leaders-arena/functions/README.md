# Deploying the application-form backend

This Cloud Function stores each "Apply now" form submission in Firestore
and emails the applicant a confirmation via SendGrid.

## 1. Create the Google Cloud project

1. Go to https://console.cloud.google.com/projectcreate and create a project
   (e.g. `youth-leaders-arena`). Note the **project ID**.
2. Enable billing on the project (Cloud Functions/Firestore have a free
   tier, but billing must be enabled to deploy).
3. Enable the required APIs:
   ```
   gcloud config set project YOUR_PROJECT_ID
   gcloud services enable cloudfunctions.googleapis.com \
     cloudbuild.googleapis.com \
     firestore.googleapis.com \
     run.googleapis.com
   ```

## 2. Create the Firestore database

1. Console → Firestore → **Create database** → Native mode → pick a region.
2. That's it — the function creates the `applications` collection
   automatically on first write. Each document stores: `name`, `age`,
   `email`, `phone`, `program`, `reason`, `submittedAt`.

## 3. Set up SendGrid

1. Sign up at https://signup.sendgrid.com/ (free tier: 100 emails/day).
2. **Settings → Sender Authentication** → verify the email address (or
   domain) you'll send confirmations from.
3. **Settings → API Keys → Create API Key** (Restricted Access → Mail Send).
   Copy the key — you won't see it again.

## 4. Configure environment variables

Copy `submitApplication/.env.example` to `submitApplication/.env` and fill in:

```
SENDGRID_API_KEY=SG.xxxxxxxx
FROM_EMAIL=verified-sender@yourdomain.com
ALLOWED_ORIGIN=https://your-site-domain.com
```

`ALLOWED_ORIGIN` should be the exact origin your site is served from
(e.g. `https://yourname.github.io`), so only your site can call the
function. Use `*` while testing locally, but lock it down before going live.

## 5. Deploy

From `functions/submitApplication/`:

```
gcloud functions deploy submitApplication \
  --gen2 \
  --runtime=nodejs20 \
  --region=us-central1 \
  --source=. \
  --entry-point=submitApplication \
  --trigger-http \
  --allow-unauthenticated \
  --set-env-vars-file=.env
```

(`gcloud` reads `.env` as `KEY=VALUE` pairs for `--set-env-vars-file`; if
your CLI version doesn't support that flag, pass each var individually
with `--set-env-vars SENDGRID_API_KEY=...,FROM_EMAIL=...,ALLOWED_ORIGIN=...`.)

The command prints a **trigger URL** like:
```
https://us-central1-YOUR_PROJECT_ID.cloudfunctions.net/submitApplication
```

## 6. Point the frontend at it

In `script.js`, set:

```js
var SUBMIT_ENDPOINT = 'https://us-central1-YOUR_PROJECT_ID.cloudfunctions.net/submitApplication';
```

## 7. Test

Open `index.html`, fill out the Apply form, submit. You should see a
"Thanks! Your application was submitted" message, a new document in the
Firestore `applications` collection, and a confirmation email in the
applicant's inbox (check spam while the sender domain is new/unverified
by mail providers).
