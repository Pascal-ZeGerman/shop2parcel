<!-- generated-by: gsd-doc-writer -->
# Getting Started with Shop2Parcel

This guide walks you from a fresh Home Assistant instance to a working Shop2Parcel integration — sensors appearing for your Shopify shipments, tracking forwarded to Parcel automatically.

Two connection paths are available. Choose the one that fits your setup:

- **Gmail OAuth2** — connects via the Gmail API using a Google Cloud OAuth2 credential. More setup steps upfront, but no password to manage and access is scoped to read-only.
- **IMAP** — connects to any IMAP server (Gmail, Outlook, iCloud, self-hosted, etc.) with a username and password (or app password). Simpler to configure if IMAP is already enabled on your account.

Both paths require a [Parcel](https://web.parcelapp.net) account with an API key.

---

## Prerequisites

### Home Assistant and HACS

- Home Assistant `2025.1.0` or later
- HACS installed in Home Assistant (required for custom repository installation)

### Gmail OAuth2 path

- Gmail account that receives Shopify shipping confirmation emails
- Google Cloud project with the Gmail API enabled and OAuth2 credentials created (see [Gmail OAuth2 Setup](#gmail-oauth2-setup) below)

### IMAP path

- Any email account accessible via IMAP with IMAP access enabled
- App password if your provider requires two-factor authentication (required for Gmail IMAP with 2FA; required for iCloud)

### Both paths

- Parcel account at [web.parcelapp.net](https://web.parcelapp.net) with an API key

---

## Step 1 — Install via HACS

1. Open **HACS** in your Home Assistant sidebar.
2. Click the three-dot menu (top right) and select **Custom repositories**.
3. Enter `https://github.com/Pascal-ZeGerman/shop2parcel`, set the category to **Integration**, and click **Add**.
4. Find **Shop2Parcel** in the integrations list and click **Download**.
5. Restart Home Assistant.

---

## Step 2 — Get your Parcel API key

Both setup paths require a Parcel API key before you can complete the wizard.

1. Sign in at [web.parcelapp.net](https://web.parcelapp.net).
2. Go to **Settings → API Key**.
3. Copy the key — you will paste it into the Shop2Parcel setup wizard in the final step.

> The free Parcel tier allows up to 20 new tracking numbers per day. If you exceed this, Shop2Parcel pauses forwarding new shipments until midnight UTC and then resumes automatically. Existing tracked shipments are not affected.

---

## Step 3 — Add the Integration

Go to **Settings → Devices & Services → + Add Integration** and search for **Shop2Parcel**.

The setup wizard opens with a connection type picker. Choose your path and follow the steps below.

---

## Gmail OAuth2 Setup

Complete this section before starting the setup wizard if you are using the Gmail path.

### A. Create a Google Cloud project and enable the Gmail API

1. Go to [Google Cloud Console](https://console.cloud.google.com/).
2. Click the project selector at the top and choose **New Project**. Name it (e.g., `shop2parcel-ha`) and click **Create**.
3. In the left menu go to **APIs & Services → Library**.
4. Search for **Gmail API** and click **Enable**.

### B. Configure the OAuth consent screen

1. In the left menu go to **APIs & Services → OAuth consent screen**.
2. Choose **External** and click **Create**.
3. Fill in the required fields: App name (`Shop2Parcel`), User support email, Developer contact email. Click **Save and Continue**.
4. On the **Scopes** page click **Add or Remove Scopes**, search for `gmail.readonly`, check it, and click **Update**. Click **Save and Continue**.
5. On the **Test users** page add your own Gmail address. Click **Save and Continue**.
6. Review and click **Back to Dashboard**.

### C. Create OAuth2 client credentials

1. In the left menu go to **APIs & Services → Credentials**.
2. Click **+ Create Credentials → OAuth client ID**.
3. Choose application type **Web application**. Name it (e.g., `shop2parcel-ha`) and click **Create**.
4. Copy the **Client ID** and **Client Secret** shown in the dialog.

> Keep your Client Secret private — never commit it to git or share it publicly.

### D. Enter credentials in Home Assistant

Before the setup wizard can start the OAuth2 flow, Home Assistant needs your Client ID and Secret in its Application Credentials store.

When you select **Gmail** in the connection type picker, Home Assistant will prompt you to enter your **Client ID** and **Client Secret** in an Application Credentials dialog (or direct you to **Settings → Application Credentials** if they are not yet stored).

### E. Complete the OAuth2 consent flow

After the credentials are saved, Home Assistant opens a Google OAuth2 consent screen in your browser. You may see an "unverified app" warning — this is expected for a personal OAuth2 application. Click **Advanced → Go to Shop2Parcel (unsafe)** to proceed and grant the `gmail.readonly` scope.

### F. Enter your Parcel API key

After the OAuth2 consent completes, the wizard shows a final form. Paste your Parcel API key and confirm the entry name (pre-filled as `Shop2Parcel (your@gmail.com)`). Click **Submit**.

The integration validates the API key against the parcelapp.net view-deliveries endpoint. If validation fails, check that you copied the key correctly.

---

## IMAP Setup

### A. Enable IMAP on your email account

- **Gmail:** Go to **Gmail Settings → See all settings → Forwarding and POP/IMAP → Enable IMAP**. If your Google account has 2FA enabled, generate an App Password at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords) and use that as the password.
- **Outlook / Hotmail:** IMAP is enabled by default. Use your Microsoft account password or, for accounts with 2FA, generate an App Password.
- **iCloud:** Generate an app-specific password at [appleid.apple.com](https://appleid.apple.com). Use `imap.mail.me.com` as the host.
- **Self-hosted:** Confirm IMAP is enabled in your mail server configuration.

### B. Enter IMAP credentials in the wizard

Select **IMAP** in the connection type picker. The wizard shows a single form with these fields:

| Field | Description | Default |
|-------|-------------|---------|
| IMAP host | Hostname of your IMAP server (e.g., `imap.gmail.com`, `imap.outlook.com`) | — |
| Port | IMAP port number | `993` |
| Username | Your email address | — |
| Password | Your account password or app password | — |
| TLS mode | `ssl`, `starttls`, or `none` | `ssl` |

Port `993` with TLS mode `ssl` is correct for most providers. Use port `143` for `starttls` or `none`.

After you submit, Shop2Parcel performs a live connection test against the IMAP server. If it fails, an error is shown — check the hostname, port, credentials, and TLS mode.

### C. Enter your Parcel API key

On success, the wizard proceeds to the final form. Paste your Parcel API key and confirm the entry name (pre-filled as `Shop2Parcel (username@host)`). Click **Submit**.

---

## First Run

After setup completes, Shop2Parcel creates a device entry and begins polling on the default 30-minute interval. Within the first poll cycle, any Shopify shipping confirmation emails already in the inbox (within the default lookback window) are processed and forwarded to Parcel.

Sensor entities appear under **Settings → Devices & Services → Shop2Parcel**:

- `sensor.shop2parcel_<order_number>` — one per tracked shipment
- `binary_sensor.shop2parcel_has_active_shipments` — `on` when at least one active shipment is present
- Six diagnostic sensors tracking poll statistics and scan activity

To trigger an immediate poll without waiting 30 minutes, go to **Settings → Devices & Services → Shop2Parcel → three-dot menu → Reload**.

---

## Common Setup Issues

**"Invalid auth" error during IMAP setup**

The username/password combination was rejected by the IMAP server. If your account uses 2FA, you must use an app password rather than your regular account password. Gmail and iCloud both require this.

**"Cannot connect" error during IMAP setup**

The IMAP server was unreachable. Check:
- Hostname spelling (e.g., `imap.gmail.com` not `smtp.gmail.com`)
- Port number matches the TLS mode (`993` for `ssl`, `143` for `starttls` or `none`)
- IMAP is enabled in your email provider settings

**"Invalid API key" error in the Parcel step**

The parcelapp.net API key was not accepted. Verify you copied the full key from **web.parcelapp.net → Settings → API Key** without trailing spaces.

**Google "unverified app" warning during OAuth2**

This is expected. Your Google Cloud project is in testing mode and is not verified by Google. Click **Advanced → Go to Shop2Parcel (unsafe)** to proceed. Only your own Gmail address (added as a test user in step B5) can authorize.

**Gmail OAuth2 reauth required after token revocation**

If the integration card shows an error requiring re-authentication, click **Re-authenticate** and complete the Google OAuth2 consent flow again. This happens when the Google Cloud project is modified or the test user list changes, which revokes previously issued refresh tokens.

**No shipments appear after the first poll**

1. Confirm that shipping emails from `no-reply@shopify.com` exist in the monitored inbox.
2. Enable **Debug mode** via **Settings → Devices & Services → Shop2Parcel → Configure → Debug mode**. Debug mode extends the scan window and suppresses actual Parcel POSTs — useful for verifying email parsing without consuming the 20/day quota.
3. Reload the integration and check the `sensor.shop2parcel_emails_returned` and `sensor.shop2parcel_emails_matched` values to see where emails are being filtered.
4. Download the diagnostics report (click **Download Diagnostics** on the integration card) for a detailed activity log.

---

## Next Steps

- See [CONFIGURATION.md](CONFIGURATION.md) for poll interval, Gmail search query, IMAP search criteria, and all other configurable options.
- See [ARCHITECTURE.md](ARCHITECTURE.md) for a description of how the integration works internally.
