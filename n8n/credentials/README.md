# n8n Credentials Setup

Do not commit secrets.

Required credentials:
- Gmail OAuth2 credential for inbox polling
- Google Sheets credential for contact sync
- Optional Google Drive credential for transcript ingestion
- HTTP header credential for `X-Webhook-Secret`

Set `X-Webhook-Secret` to `N8N_WEBHOOK_SECRET` in all API POST nodes.
