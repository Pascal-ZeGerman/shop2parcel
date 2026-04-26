# Shop2Parcel

A Home Assistant custom integration that polls Shopify for order shipment data and forwards it to [Parcel](https://parcelapp.net) for tracking. Shipment data from Shopify orders appears automatically in Home Assistant without manual entry.

## Prerequisites

- Shopify Custom App with Admin API access (Access Token)
- Parcel account with API key
- Home Assistant 2025.1 or later
- HACS installed in Home Assistant

## Installation via HACS

1. Open HACS in Home Assistant.
2. Click the three-dot menu (top right) and select "Custom Repositories".
3. Enter `https://github.com/Pascal-ZeGerman/shop2parcel` and select category "Integration".
4. Click "Add", then find "Shop2Parcel" in the integrations list and install it.
5. Restart Home Assistant.

## Configuration

After restarting, go to Settings → Devices & Services → Add Integration → search "Shop2Parcel". Follow the setup wizard to authenticate with Google (for Gmail access), then enter your Parcel API key.

## Status

Early development — core config flow and entry setup are implemented. Expect rough edges.

## License

MIT
