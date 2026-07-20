# Deploying the application-form backend

This Cloud Function stores each "Apply now" form submission in Firestore
and emails the applicant a confirmation via SendGrid. The static site
(`index.html`, `style.css`, `script.js`, `fonts/`) is hosted separately on
Namecheap under **ylarena.online** — Namecheap only serves the static
files; the form still POSTs to this Cloud Function on Google Cloud.

## 1. Create the Google Cloud project

1. Go to https://console.cloud.google.com/projectcreate and create a project
   (e.g. `ylarena`). Note the **project ID**.
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
   domain, e.g. `ylarena.online`) you'll send confirmations from.
3. **Settings → API Keys → Create API Key** (Restricted Access → Mail Send).
   Copy the key — you won't see it again.

## 4. Configure environment variables

Copy `submitApplication/.env.example` to `submitApplication/.env` and fill in:

```
SENDGRID_API_KEY=SG.xxxxxxxx
FROM_EMAIL=verified-sender@ylarena.online
ALLOWED_ORIGIN=https://ylarena.online,https://www.ylarena.online
```

`ALLOWED_ORIGIN` is a comma-separated list — both the apex domain and the
`www` subdomain are allowed to call the function so it works either way
visitors reach the site. Use `*` while testing locally, but keep the real
domains for production.

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

Use `--set-env-vars-file=.env` (not `--set-env-vars`) — the `ALLOWED_ORIGIN`
value itself contains a comma, which would otherwise be misread as two
separate `--set-env-vars` entries.

The command prints a **trigger URL** like:
```
https://us-central1-YOUR_PROJECT_ID.cloudfunctions.net/submitApplication
```

## 6. Point the frontend at it

In `script.js`, set:

```js
var SUBMIT_ENDPOINT = 'https://us-central1-YOUR_PROJECT_ID.cloudfunctions.net/submitApplication';
```

## 7. Upload the static site to Namecheap

Since you have Namecheap shared hosting with cPanel:

1. Log into Namecheap → **Hosting List** → **Go to cPanel** for ylarena.online.
2. Open **File Manager** → go to `public_html/` (this is the web root for
   the domain; if ylarena.online is an addon domain, its root may instead be
   `public_html/ylarena.online/` — check under cPanel → **Domains**).
3. Upload `index.html`, `style.css`, `script.js`, and the `fonts/` folder
   (with all three `.woff2` files inside) directly into that root — either
   drag-and-drop in File Manager's **Upload** button, or connect with an
   FTP client (FileZilla) using the FTP credentials from cPanel →
   **FTP Accounts** and upload the same files.
4. Visit `https://ylarena.online` — the site should load. If it shows the
   default cPanel placeholder page instead, double check the files landed
   in the correct root folder and that `index.html` is directly inside it
   (not nested one level deeper).
5. cPanel hosting normally issues a free SSL certificate (AutoSSL)
   automatically within a few minutes to hours of the domain resolving —
   if `https://` doesn't work yet, check cPanel → **SSL/TLS Status**.

## 8. Test

Open `https://ylarena.online`, fill out the Apply form, submit. You should
see a "Thanks! Your application was submitted" message, a new document in
the Firestore `applications` collection, and a confirmation email in the
applicant's inbox (check spam while the sender domain is new/unverified by
mail providers).
