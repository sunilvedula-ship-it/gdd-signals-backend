# Alice Blue Live Execution Setup

The application uses Alice Blue Vendor SSO. App users never enter or store their own API secret.

## Alice Blue Vendor App

Configure this redirect URL in the Alice Blue developer portal:

`https://gdd-signals-backend.onrender.com/api/broker/aliceblue/callback`

Record the approved App Code, API Secret and Algo ID supplied for the application.

## Server Configuration

Set these production environment variables:

```text
PUBLIC_BACKEND_URL=https://gdd-signals-backend.onrender.com
CREDENTIAL_ENCRYPTION_KEY=<long-random-secret>
BROKER_AUTH_STATE_SECRET=<different-long-random-secret>
ALICEBLUE_APP_CODE=<approved-app-code>
ALICEBLUE_API_SECRET=<approved-api-secret>
ALICEBLUE_ALGO_ID=<approved-algo-id>
ALICEBLUE_API_ORDER_SOURCE=API
ALICEBLUE_MAX_ORDERS_PER_SECOND=9
MAX_LIVE_LOTS_PER_ORDER=10
ADMIN_EMAILS=sunilvedula@gmail.com
LIVE_TRADING_ENABLED=false
WEBHOOK_AUTO_EXECUTION_MODE=PAPER
ALLOW_SANDBOX_AUTH=false
```

Use a server with a dedicated outbound IP. If static egress is supplied through an authenticated
proxy, set `BROKER_PROXY_URL` to that proxy URL. Register the resulting outbound IP with Alice Blue
and the exchange process before UAT.

## Activation Checklist

1. Keep `LIVE_TRADING_ENABLED=false` while credentials and IP registration are incomplete.
2. Connect a dedicated Alice Blue UAT account from the app Settings screen.
3. Confirm NIFTY and BANKNIFTY futures and options contract previews.
4. Place one-lot limit orders and verify order IDs, fills, rejections and partial fills.
5. Verify manual exits and session expiry behavior.
6. Confirm Alice Blue order tags, Algo ID and static source IP in broker logs.
7. Set `LIVE_TRADING_ENABLED=true` only after Alice Blue accepts UAT.

`WEBHOOK_AUTO_EXECUTION_MODE` remains `PAPER`. Live orders require an authenticated user, current
daily consent, an Alice Blue SSO session, a signed 90-second order preview and explicit confirmation
inside the app.
