[![pytest](https://github.com/Pascal-ZeGerman/shop2parcel/actions/workflows/pytest.yml/badge.svg)](https://github.com/Pascal-ZeGerman/shop2parcel/actions/workflows/pytest.yml)
[![hassfest](https://github.com/Pascal-ZeGerman/shop2parcel/actions/workflows/hassfest.yml/badge.svg)](https://github.com/Pascal-ZeGerman/shop2parcel/actions/workflows/hassfest.yml)
[![hacs](https://github.com/Pascal-ZeGerman/shop2parcel/actions/workflows/hacs.yml/badge.svg)](https://github.com/Pascal-ZeGerman/shop2parcel/actions/workflows/hacs.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)

# Shop2Parcel

A Home Assistant custom integration that monitors your Gmail inbox for Shopify shipping confirmation emails and automatically forwards tracking information to [Parcel](https://parcelapp.net). Each shipment appears as a sensor entity in Home Assistant — no manual entry required.

## What it does

1. Polls your Gmail inbox on a configurable schedule (default: every 30 minutes).
2. Finds Shopify shipping confirmation emails (from `no-reply@shopify.com`, subject contains "shipped").
3. Extracts the tracking number and carrier from the email.
4. Posts the shipment to parcelapp.net via its API so you can track it in the Parcel app.
5. Creates a `sensor.shop2parcel_<order_number>` entity in Home Assistant showing current shipment status.

## Prerequisites

- Gmail account that receives Shopify shipping confirmation emails
- Google Cloud project with Gmail API enabled (see setup below)
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

After restarting, go to **Settings → Devices & Services → + Add Integration** and search for **Shop2Parcel**. The setup wizard will ask for:

1. **Google OAuth2 Client ID and Client Secret** — see setup guide below.
2. **Parcel API key** — see setup guide below.

Once entered, Home Assistant will open a Google OAuth2 consent screen in your browser. You may see an "unverified app" warning — this is expected for a personal OAuth2 app. Click **Advanced → Go to Shop2Parcel (unsafe)** to proceed and grant the `gmail.readonly` scope.

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
3. Choose application type **Desktop app** (not "Web application" — Desktop app is required for Home Assistant's local OAuth2 redirect).
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
| Poll interval (minutes) | 30 | How often to check Gmail. Minimum 5 minutes. |
| Gmail search query | `from:no-reply@shopify.com subject:shipped` | Advanced: customise the Gmail filter for non-standard senders. |

---

## Sensor entities

Each tracked shipment creates a sensor:

- **Entity ID:** `sensor.shop2parcel_<order_number>`
- **State:** `in_transit`, `delivered`, or `unknown`
- **Attributes:** `tracking_number`, `carrier`, `order_number`, `tracking_url`

Delivered shipments are removed from the sensor list automatically after 24 hours.

---

## Known limitations

- **Single Gmail account:** v0.1.0 supports one Gmail account per HA instance.
- **Parcel quota:** 20 new shipments per day maximum on the free Parcel tier.
- **Poll interval:** Near-real-time tracking requires a shorter poll interval; 30 minutes is the default to avoid Gmail API quota.
- **Carrier mapping:** Unknown carriers are mapped to a placeholder; tracking in Parcel may show limited status.
- **Shopify email format:** Parsing depends on Shopify's standard email template. Heavily customised store emails may not parse correctly.

---

## License

MIT
