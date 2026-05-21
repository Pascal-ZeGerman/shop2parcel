[![pytest](https://github.com/Pascal-ZeGerman/shop2parcel/actions/workflows/pytest.yml/badge.svg)](https://github.com/Pascal-ZeGerman/shop2parcel/actions/workflows/pytest.yml)
[![hassfest](https://github.com/Pascal-ZeGerman/shop2parcel/actions/workflows/hassfest.yml/badge.svg)](https://github.com/Pascal-ZeGerman/shop2parcel/actions/workflows/hassfest.yml)
[![hacs](https://github.com/Pascal-ZeGerman/shop2parcel/actions/workflows/hacs.yml/badge.svg)](https://github.com/Pascal-ZeGerman/shop2parcel/actions/workflows/hacs.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)

# Shop2Parcel

A Home Assistant custom integration that monitors your email inbox (Gmail OAuth2 or IMAP) for Shopify shipping confirmation emails and automatically forwards tracking information to [Parcel](https://parcelapp.net). Each shipment appears as a sensor entity in Home Assistant — no manual entry required.

## What it does

1. Polls your email inbox on a configurable schedule (default: every 30 minutes) via Gmail OAuth2 or IMAP.
2. Finds Shopify shipping confirmation emails (from `no-reply@shopify.com`, subject contains "shipped").
3. Extracts the tracking number and carrier from the email.
4. Posts the shipment to parcelapp.net via its API so you can track it in the Parcel app.
5. Creates a `sensor.shop2parcel_<order_number>` entity in Home Assistant showing current shipment status.

## Prerequisites

### Gmail OAuth2

- Gmail account that receives Shopify shipping confirmation emails
- Google Cloud project with Gmail API enabled (see setup below)

### IMAP

- Any email account accessible via IMAP (Gmail, Outlook, iCloud, self-hosted, etc.)
- IMAP access enabled for the account
- App password if your provider requires two-factor authentication (recommended for Gmail IMAP)

### Common

- Parcel account with API key (`web.parcelapp.net`)
- Home Assistant 2025.1 or later
- HACS installed in Home Assistant

## Installation via HACS

1. Open HACS in Home Assistant.
2. Click the three-dot menu (top right) and select **Custom repositories**.
3. Enter `https://github.com/Pascal-ZeGerman/shop2parcel`, select category **Integration**, and click **Add**.
4. Find **Shop2Parcel** in the integrations list and click **Download**.
5. Restart Home Assistant.

## Configuration

After restarting, go to **Settings → Devices & Services → + Add Integration** and search for **Shop2Parcel**. The setup wizard will first ask you to choose a connection type:

### Option A: Gmail OAuth2

1. **Google OAuth2 Client ID and Client Secret** — see setup guide below.
2. Home Assistant will open a Google OAuth2 consent screen in your browser. You may see an "unverified app" warning — this is expected for a personal OAuth2 app. Click **Advanced → Go to Shop2Parcel (unsafe)** to proceed and grant the `gmail.readonly` scope.
3. **Parcel API key** — see setup guide below.

### Option B: IMAP

1. Enter your **IMAP server hostname** (e.g. `imap.gmail.com`, `imap.outlook.com`), **port** (993 for SSL), **username** (your email address), **password** (or app password), and **TLS mode**.
2. **Parcel API key** — see setup guide below.

You can run setup again to add a second account (Gmail or IMAP) as a separate integration entry.

---

## Setup: Google Cloud OAuth2

Shop2Parcel reads your Gmail inbox using the Gmail API. You need a Google Cloud OAuth2 credential to authorise access.

### 1. Create a Google Cloud project and enable Gmail API

1. Go to [Google Cloud Console](https://console.cloud.google.com/).
2. Click the project selector at the top and choose **New Project**. Give it a name (e.g., `shop2parcel-ha`) and click **Create**.
3. In the left menu go to **APIs & Services → Library**.
4. Search for **Gmail API** and click **Enable**.

### 2. Configure the OAuth consent screen

1. In the left menu go to **APIs & Services → OAuth consent screen**.
2. Choose **External** and click **Create**.
3. Fill in the required fields (App name: `Shop2Parcel`, User support email, Developer contact). Click **Save and Continue**.
4. On the **Scopes** page click **Add or Remove Scopes**, search for `gmail.readonly`, check it, and click **Update**. Click **Save and Continue**.
5. On the **Test users** page add your own Gmail address. Click **Save and Continue**.
6. Review and click **Back to Dashboard**.

### 3. Create OAuth2 client credentials

1. In the left menu go to **APIs & Services → Credentials**.
2. Click **+ Create Credentials → OAuth client ID**.
3. Choose application type **Web application**.
4. Give it a name (e.g., `shop2parcel-ha`) and click **Create**.
5. Copy the **Client ID** and **Client Secret** shown in the dialog.

> Keep your Client Secret private. Never commit it to git or paste it into a public issue.

---

## Setup: Parcel API key

### Get your Parcel API key

1. Sign in at [web.parcelapp.net](https://web.parcelapp.net).
2. Go to **Settings → API Key**.
3. Copy your API key.

Note: The free Parcel tier allows up to 20 new tracking numbers per day. If you exceed this, Shop2Parcel will pause posting new shipments until midnight UTC and then resume automatically. Existing tracked shipments are not affected.

---

## Options (after setup)

After the integration is configured you can adjust settings via **Settings → Devices & Services → Shop2Parcel → Configure**:

| Option | Default | Notes |
|--------|---------|-------|
| Poll interval (minutes) | 30 | How often to check the inbox. Minimum 5 minutes. |
| Gmail search query | `from:no-reply@shopify.com subject:shipped` | (Gmail only) Advanced: customise the Gmail filter for non-standard senders. |
| IMAP search criteria | `SUBJECT "shipped"` | (IMAP only) Standard IMAP SEARCH criteria string for filtering messages. |

---

## Sensor entities

Each tracked shipment creates a sensor:

- **Entity ID:** `sensor.shop2parcel_<order_number>`
- **State:** `in_transit`, `delivered`, or `unknown`
- **Attributes:** `tracking_number`, `carrier`, `order_number`, `tracking_url`

Delivered shipments are removed from the sensor list automatically after 24 hours.

---

## Known limitations

- **Multiple accounts:** You can add multiple Gmail or IMAP accounts by running setup again.
- **Parcel quota:** 20 new shipments per day maximum on the free Parcel tier.
- **Poll interval:** Near-real-time tracking requires a shorter poll interval; 30 minutes is the default to avoid Gmail API quota.
- **Carrier mapping:** Unknown carriers are mapped to a placeholder; tracking in Parcel may show limited status.
- **Shopify email format:** Parsing depends on Shopify's standard email template. Heavily customised store emails may not parse correctly.

---

## License

MIT

---

## Diagnostic sensors

Shop2Parcel registers six diagnostic sensor entities per config entry. They are visible under the Shop2Parcel device in **Settings → Devices & Services → Shop2Parcel**. All are classified as `diagnostic` and reset to zero when Home Assistant restarts.

| Entity ID | What it measures |
|-----------|-----------------|
| `sensor.shop2parcel_emails_returned` | Total emails returned by Gmail/IMAP before deduplication. Attributes include the query used, effective query, last-poll counts, and poll duration in milliseconds. |
| `sensor.shop2parcel_new_emails_inspected` | Emails that passed the deduplication check and were inspected for shipment data. |
| `sensor.shop2parcel_emails_matched` | Emails that produced a recognised shipment (tracking number + carrier extracted). Attributes include last-poll skip reasons. |
| `sensor.shop2parcel_tracking_numbers_found` | Cumulative tracking numbers extracted across all polls. |
| `sensor.shop2parcel_keyword_hits` | Cumulative fallback regex matches (broad-scan arm). Attributes show per-keyword hit counts. |
| `sensor.shop2parcel_activity_log` | Count of all scan events since last restart. The `recent_events` attribute holds the last 10 scan events as a list of dicts. |

A `binary_sensor.shop2parcel_has_active_shipments` entity is also created. It is `on` when at least one shipment is present in the coordinator and `off` when the shipment list is empty.

### Downloading diagnostics

The full diagnostic report (config, poll stats, activity log, and the 10 most recent shipments) can be downloaded without credentials by clicking **Download Diagnostics** on the integration card. API keys and passwords are automatically redacted from the download.

---

## Debug mode (dry run)

Debug mode lets you verify that email parsing works without posting anything to parcelapp.net. Enable it via **Settings → Devices & Services → Shop2Parcel → Configure → Debug mode**.

When debug mode is active:

- No parcels are posted to parcelapp.net (all POST calls are suppressed).
- The Gmail scan window is extended to the maximum (365 days) so historical emails are re-scanned.
- A persistent notification labelled **Shop2Parcel Debug Mode** appears in the Home Assistant UI after each poll, showing how many emails were scanned that cycle.
- Activity log events record `dry_run_suppressed` outcomes instead of `posted`.
- Detailed log lines are emitted at `INFO` level (visible without enabling debug logging) showing the email subject, sender, candidate tokens, and outcome for each inspected message.

Disable debug mode when you are ready to forward real shipments. The persistent notification is dismissed automatically when debug mode is turned off.

---

## Carrier support

Shop2Parcel maps Shopify carrier names to parcelapp.net carrier codes. The following carriers are recognised automatically:

| Shopify carrier name | Parcel carrier code |
|----------------------|---------------------|
| UPS | `ups` |
| FedEx | `fedex` |
| USPS | `usps` |
| DHL Express | `dhl` |
| DHL eCommerce | `dhl` |
| Canada Post | `cp` |
| Royal Mail | `rm` |
| Australia Post | `au` |
| Japan Post (EN) | `jp` |
| La Poste | `lp` |
| PostNL | `tntp` |
| TNT | `tnt` |
| GLS | `gls` |
| DPD | `dpd` |
| Poste Italiane | `it` |

Carriers not in the table above are mapped to the `pholder` code, which is a valid parcelapp.net placeholder. Tracking status in the Parcel app will be limited for unrecognised carriers, but the shipment will still appear and the API call will succeed.

The carrier matching is case-insensitive.

---

## Troubleshooting

**No shipments appear after setup**

1. Confirm that shipping confirmation emails exist in the monitored inbox. Check the inbox directly and look for emails from `no-reply@shopify.com` with "shipped" in the subject.
2. In Gmail mode, verify that the search query in **Options** matches those emails. You can paste the query directly into the Gmail search bar to test it.
3. Enable **Debug mode** (see above) and trigger a manual poll by reloading the integration (**Settings → Devices & Services → Shop2Parcel → three-dot menu → Reload**). Check the `sensor.shop2parcel_emails_returned` and `sensor.shop2parcel_emails_matched` values to narrow down where emails are being dropped.
4. Download the diagnostics report for a detailed activity log.

**Emails are found but no sensor is created**

The email was parsed but the tracking number was likely already submitted in a previous poll. Check `sensor.shop2parcel_activity_log` attributes for `dry_run_suppressed` or `already_submitted` outcomes. If the sensor was deleted from the entity registry, reload the integration to re-create it.

**Parcel quota exceeded**

When the 20/day limit is reached, the integration logs a warning and stores the quota-exhausted timestamp. No new shipments will be forwarded until midnight UTC. Existing sensor entities continue to update. The quota resets automatically — no action is required.

**Re-authentication required (Gmail)**

If the integration enters a re-auth state (shown as an error on the integration card), click **Re-authenticate** and complete the Google OAuth2 consent flow again. This happens when the refresh token is revoked (e.g., the Google Cloud project is modified or the test user list is changed).

**IMAP connection fails**

- Confirm the hostname, port, and TLS settings. Port 993 uses SSL/TLS; port 143 uses STARTTLS or no TLS.
- For Gmail IMAP, ensure "Less secure app access" or an app password is configured. Google account 2FA requires an app password.
- For iCloud, use an app-specific password generated at appleid.apple.com.
