<!-- generated-by: gsd-doc-writer -->
# Getting Started

This guide walks you through installing and configuring Shop2Parcel for the first time. By the end you will have the integration running, shipping confirmation emails forwarded to Parcel, and at least one shipment sensor visible in Home Assistant.

---

## Prerequisites

### Home Assistant

- Home Assistant **2025.1.0 or later** (declared in `hacs.json`)
- HACS (Home Assistant Community Store) installed — [HACS installation guide](https://hacs.xyz/docs/setup/download)

### Parcel account

- A [Parcel](https://parcelapp.net) account at [web.parcelapp.net](https://web.parcelapp.net)
- Your **Parcel API key** — found in the Parcel web app under **Settings → API**
- The free Parcel tier allows up to **20 new tracking numbers per day**. Shop2Parcel pauses forwarding when this limit is reached and resumes automatically at midnight UTC.

### Email access — choose one

**Option A: Gmail OAuth2 (recommended)**

- A Gmail account that receives Shopify shipping confirmation emails or direct carrier notifications
- A **Google Cloud project** with the Gmail API enabled and OAuth2 credentials created (see [Gmail OAuth2 setup](#step-2-gmail-oauth2-setup) below)

**Option B: IMAP**

- Any email account accessible via IMAP (Gmail, Outlook, iCloud, self-hosted, etc.)
- IMAP access enabled for the account
- An **app password** if your provider requires two-factor authentication (required for Gmail IMAP with 2FA enabled)

---

## Step 1: Install via HACS

1. Open HACS in Home Assistant.
2. Click the three-dot menu (top right) and select **Custom repositories**.
3. Enter `https://github.com/Pascal-ZeGerman/shop2parcel`, select category **Integration**, and click **Add**.
4. Find **Shop2Parcel** in the integrations list and click **Download**.
5. **Restart Home Assistant** — the integration will not appear until after a restart.

---

## Step 2: Gmail OAuth2 Setup

Skip this section if you are using IMAP — go to [Step 3: Add the Integration](#step-3-add-the-integration).

Shop2Parcel reads Gmail using Google's Gmail API. You need a personal OAuth2 client credential registered in HA's Application Credentials UI before adding the integration.

### 2a. Create a Google Cloud project and enable the Gmail API

1. Go to [Google Cloud Console](https://console.cloud.google.com/).
2. Click the project selector at the top and choose **New Project**. Give it a name (e.g., `shop2parcel-ha`) and click **Create**.
3. In the left menu go to **APIs & Services → Library**.
4. Search for **Gmail API** and click **Enable**.

### 2b. Configure the OAuth consent screen

1. In the left menu go to **APIs & Services → OAuth consent screen**.
2. Choose **External** and click **Create**.
3. Fill in the required fields:
   - **App name**: `Shop2Parcel`
   - **User support email**: your own email address
   - **Developer contact information**: your own email address
4. Click **Save and Continue**.
5. On the **Scopes** page click **Add or Remove Scopes**, search for `gmail.readonly`, check it, and click **Update**. Click **Save and Continue**.
6. On the **Test users** page click **+ Add Users** and add your own Gmail address. Click **Save and Continue**.
7. Review and click **Back to Dashboard**.

> The app will show as "unverified" in the Google consent screen. This is normal for a personal app — you will be prompted to click **Advanced → Go to Shop2Parcel (unsafe)** during the OAuth2 flow in Home Assistant.

### 2c. Create OAuth2 client credentials

1. In the left menu go to **APIs & Services → Credentials**.
2. Click **+ Create Credentials → OAuth client ID**.
3. Choose application type **Web application**.
4. Give it a name (e.g., `shop2parcel-ha`) and click **Create**.
5. Copy the **Client ID** and **Client Secret** shown in the dialog.

> Keep your Client Secret private. Never commit it to git or paste it into a public issue.

### 2d. Register credentials in Home Assistant

1. In Home Assistant go to **Settings → Devices & Services**.
2. Click **Application Credentials** (top right, or search for it in the menu).
3. Click **+ Add Application Credentials** and select **Shop2Parcel** from the integration list.
4. Paste your **Client ID** and **Client Secret** and click **Create**.

You only need to do this once. The credentials are stored in HA's encrypted storage.

---

## Step 3: Add the Integration

1. Go to **Settings → Devices & Services**.
2. Click **+ Add Integration** (bottom right).
3. Search for **Shop2Parcel** and click it.
4. The setup wizard shows a **connection type** picker:

### Option A: Gmail OAuth2

1. Select **Gmail** and click **Submit**.
2. A browser pop-up opens for Google's OAuth2 consent screen. If you see an "unverified app" warning, click **Advanced → Go to Shop2Parcel (unsafe)** and grant the `gmail.readonly` scope.
3. After authorizing, the wizard returns to Home Assistant and shows the **finish** step.
4. Enter your **Parcel API key** (from [web.parcelapp.net](https://web.parcelapp.net) → Settings → API) and optionally change the entry name.
5. Click **Submit**. The integration validates the API key against parcelapp.net before saving.

### Option B: IMAP

1. Select **IMAP** and click **Submit**.
2. Enter your IMAP server details:

   | Field | Example | Notes |
   |-------|---------|-------|
   | IMAP server hostname | `imap.gmail.com` | For Outlook: `imap.outlook.com`; for iCloud: `imap.mail.me.com` |
   | Port | `993` | 993 for SSL (default); 143 for STARTTLS |
   | Username | `you@gmail.com` | Your full email address |
   | Password | — | Use an app password if your provider requires 2FA |
   | TLS mode | `ssl` | `ssl` (recommended), `starttls`, or `none` |

3. Click **Submit**. The config flow tests the IMAP connection before proceeding. If it fails, check the hostname, port, and credentials.
4. On the next step, enter your **Parcel API key** and click **Submit**.

You can run setup again to add a second account (Gmail or IMAP) as a separate integration entry.

---

## Step 4: Verify the Integration is Working

After setup completes the integration entry appears in **Settings → Devices & Services** under **Shop2Parcel**. Click the entry to open the device page.

### Expected entities

The following entities are created immediately on first load. They all belong to a single **Shop2Parcel** device per config entry.

**Binary sensor:**

| Entity | What it shows |
|--------|---------------|
| `binary_sensor.shop2parcel_has_active_shipments` | `on` when at least one shipment is in the coordinator, `off` when none are present |

**Diagnostic sensors** (visible under the device, category: Diagnostic):

| Entity | What it measures |
|--------|-----------------|
| `sensor.shop2parcel_emails_returned` | Total emails returned by Gmail/IMAP before deduplication |
| `sensor.shop2parcel_new_emails_inspected` | Emails that passed the dedup check and were parsed |
| `sensor.shop2parcel_emails_matched` | Emails that produced a recognized shipment |
| `sensor.shop2parcel_tracking_numbers_found` | Cumulative tracking numbers extracted |
| `sensor.shop2parcel_keyword_hits` | Cumulative fallback regex matches (broad-scan arm) |
| `sensor.shop2parcel_activity_log` | Count of all scan events; `recent_events` attribute holds the last 10 events |

> Entity IDs above use the default HA naming convention. If you have multiple Shop2Parcel entries, HA appends a suffix to disambiguate.

**Shipment sensors** — one per forwarded shipment:

Each shipment that is successfully parsed and forwarded to Parcel creates a new sensor entity. The sensor is named **Shipment `<order_name>`** under the Shop2Parcel device (e.g., **Shipment #1234**). The unique ID is composed as `shop2parcel_{entry_id}_{message_id}` — stable across HA restarts.

| Attribute | Value |
|-----------|-------|
| State | `in_transit` (static — no per-poll status fetch in v1) |
| `order_name` | Shopify order name (e.g., `#1234`) |
| `tracking_number` | Tracking number extracted from the email |
| `carrier` | Carrier name (e.g., `UPS`, `FedEx`, `USPS`) |
| `email_date` | Date the shipping email was received |

### Triggering a first poll

The coordinator polls on a schedule (default: every **30 minutes**). To trigger an immediate poll:

1. Go to **Settings → Devices & Services → Shop2Parcel**.
2. Click the three-dot menu on the integration card.
3. Select **Reload**.

After reload completes, check `sensor.shop2parcel_emails_returned` — if the number is greater than zero, emails are being retrieved. If `sensor.shop2parcel_emails_matched` is also non-zero, shipments have been found and forwarded to Parcel.

---

## Common First-Run Issues

### No entities appear at all

- Confirm the integration was added successfully — it should show in **Settings → Devices & Services** without an error badge.
- Restart Home Assistant if the integration entry shows a "loading" state that does not resolve.

### Diagnostic sensors show zeros after reload

The poll ran but no emails matched. Check:

1. The monitored inbox contains shipping confirmation emails from `no-reply@shopify.com` (or UPS/USPS/FedEx for direct carrier emails).
2. For Gmail: paste the Gmail search query shown in **Options** directly into the Gmail search bar to confirm it returns the expected emails.
3. Enable **Debug mode** via **Settings → Devices & Services → Shop2Parcel → Configure** to re-scan all emails without posting to Parcel — this extends the Gmail scan window to 365 days and logs detailed outcomes per email.

### "Unverified app" warning during Gmail OAuth2

This is expected for a personal Google Cloud project. Click **Advanced → Go to Shop2Parcel (unsafe)** to proceed. The `gmail.readonly` scope grants read-only access — no emails can be sent or modified.

### Gmail OAuth2: "Invalid client" error

The Client ID or Client Secret entered in Application Credentials is incorrect or copied with extra whitespace. Remove the existing Application Credentials entry and re-enter the values exactly as shown in the Google Cloud Console.

### IMAP: "invalid_auth" error

- Confirm the username (full email address) and password are correct.
- If you use Gmail with 2FA, you must use an **app password** — generate one at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords). Your regular Google password will not work.
- For iCloud accounts, generate an app-specific password at [appleid.apple.com](https://appleid.apple.com).

### IMAP: "imap_cannot_connect" error

- Verify the hostname and port. Port 993 requires TLS mode `ssl`; port 143 uses `starttls` or `none`.
- Common hostnames: Gmail → `imap.gmail.com`; Outlook/Hotmail → `imap.outlook.com`; iCloud → `imap.mail.me.com`.
- Confirm IMAP access is enabled in your email provider's settings (Gmail: **Settings → See all settings → Forwarding and POP/IMAP → Enable IMAP**).

### Parcel API key rejected

The key is validated by calling the parcelapp.net view-deliveries endpoint at setup time. If this step fails with `invalid_api_key`, re-copy the key from [web.parcelapp.net](https://web.parcelapp.net) → Settings → API and ensure no trailing spaces are included.

### Shipments found but no sensor created

The tracking number was previously submitted and the dedup check skipped it. Check `sensor.shop2parcel_activity_log` attributes — the `recent_events` list shows the outcome for each inspected email (`already_added` entries indicate duplicates). If the sensor was previously removed from the entity registry, reload the integration to re-register it.

### Re-authentication required (Gmail)

If the integration card shows an error badge with a re-authenticate prompt, click **Re-authenticate** and complete the Google consent flow again. This occurs when the refresh token is revoked — typically because the Google Cloud project was modified or the test user list was changed after initial authorization.

---

## Next Steps

- See [CONFIGURATION.md](CONFIGURATION.md) for all credential fields, options, and per-environment settings.
- See [ARCHITECTURE.md](ARCHITECTURE.md) for a component diagram and data flow description.
- Adjust the poll interval or Gmail query via **Settings → Devices & Services → Shop2Parcel → Configure**.
- Enable **Debug mode** to verify email parsing before forwarding live shipments to Parcel.
