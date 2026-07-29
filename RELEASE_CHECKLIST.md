# eSIMLime Release Checklist

## Live Links

Mini App:
https://esimlime-miniapp-production.up.railway.app

GitHub Mini App:
https://github.com/fredi056/esimlime-miniapp

GitHub bot:
https://github.com/fredi056/esim-bot

Railway:

```text
MINI_APP_URL=https://esimlime-miniapp-production.up.railway.app
```

## Price Update Process

1. Change only `country_prices.json` in the bot repository.
2. Validate that the JSON is correct.
3. Run `scripts/sync_miniapp_prices.ps1`.
4. Wait for the bot and Mini App deployments to finish.
5. Check one country and one test order.

Do not manually edit the Mini App copy of prices:

```text
src/data/country_prices.json
```

The bot repository `country_prices.json` is the only source of truth.

## Rollback

Stable tag before the price increase:

```text
mvp-live-before-price-increase-2026-07-29
```

To inspect the saved version:

```powershell
git show mvp-live-before-price-increase-2026-07-29
```

To roll back manually, deploy the commit pointed to by that tag in the bot and Mini App repositories.
