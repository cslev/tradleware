# Security Policy

Tradleware executes trades with your exchange credentials on infrastructure you own. There
is no Tradleware service to attack — every vulnerability affects the people running their
own instance, which is why reports are taken seriously and handled privately first.

## Reporting a vulnerability

**Please do not open a public issue for a security problem.** A public report tells everyone
running an unpatched instance exactly what to try before they have a fix.

Two private channels, either is fine:

- **[GitHub Security Advisories](https://github.com/cslev/tradleware/security/advisories/new)** —
  preferred. Private between you and the maintainer, and lets a fix and an advisory be
  prepared together.
- **Email:** [cslev@cslev.vip](mailto:cslev@cslev.vip) — if you would rather not use GitHub,
  or the advisory form is unavailable to you.

Please include:

- What an attacker can achieve, not only what looks wrong
- The steps to reproduce it, ideally the smallest request or config that shows it
- The version (`TRADLEWARE_VERSION` in the dashboard footer) and roughly how it is deployed
  — direct, behind a reverse proxy, Cloudflare Tunnel, LAN-only

This is a personal open-source project maintained in spare time, so there is no bug bounty
and no guaranteed response time. Reports are read and acted on; expect an initial reply
within a few days rather than hours.

## Supported versions

| Version | Supported |
|---|---|
| v3.4.0b (latest) | ✅ |
| Earlier releases | ❌ — upgrade to the latest |

Only the latest release receives fixes. Older Docker tags stay on Docker Hub for
reproducibility, not because they are maintained.

## Scope

**In scope** — anything in this repository: the FastAPI application, the dashboard, the
webhook endpoint, the trader classes, the bundled Docker image and the shipped
configuration defaults.

**Out of scope:**

- Exchange or broker APIs themselves, and the `ib-gateway` image, which is third-party
- An instance exposed to the internet without TLS, or with `.env` defaults unchanged — see
  the checklist below
- Anything requiring an attacker to already have your exchange keys, your `.env`, or shell
  access to the host
- The trusted-IP bypass behaving as documented: `TRUSTED_IPS` means "trust anything at this
  address", so a compromised device on that address is inside by design

## Before exposing an instance to the internet

Most real incidents come from configuration, not code. In rough order of importance:

- [ ] **Terminate TLS in front of Tradleware** and set `TRUSTED_PROXIES` to the address your
      proxy connects from. Tradleware does not serve HTTPS itself. Without this, webhooks
      are refused and forwarded headers are ignored — see
      [Webhooks must use HTTPS](WEBHOOKS.md#webhooks-must-use-https).
- [ ] **Change `DASHBOARD_PASSWORD`** from `changeme`. The login page warns while it is
      still the default.
- [ ] **Randomise `WEBHOOK_PATH`** — `pwgen -n 14 1`. The dashboard warns while it is not.
- [ ] **Generate each bot's `tradleware_api_key`** with `openssl rand -hex 32`, one per bot.
      Tradleware grades these at startup and flags weak, reused, or placeholder keys.
- [ ] **Scope `TRUSTED_IPS` tightly**, or leave it empty. It bypasses the login entirely.
- [ ] **Set `SESSION_SECRET_KEY`**, or sessions are invalidated on every restart.
- [ ] **Keep the host clock accurate** (`sudo timedatectl set-ntp true`). Signal freshness is
      measured against it.
- [ ] **Never commit `bot_configs/*.yaml` or `.env`** — both are gitignored; keep it that way.

The startup log states which mode each control is in and warns about the combinations that
silently stop signals from being accepted. Read it after any configuration change.

## What Tradleware already does

Defaults are chosen so an unmodified instance is safe; none of this needs configuring:

- Forwarded headers are trusted only from configured proxies
- Webhooks require TLS, a current timestamp, and are accepted only once
- Trade execution is serialised per bot
- Credentials are excluded from logs, notifications and the dashboard
- Session cookies are `Secure`, `HttpOnly`, `SameSite=lax`, with a bounded lifetime
- Credential comparison is constant-time
- Repeated failed authentications from one address are throttled

See the [changelog](CHANGELOG.md) for what each release changed.

## Disclosure

Once a fix is released, an advisory is published crediting the reporter unless anonymity is
preferred. Changelog entries deliberately describe *what a release adds* rather than how a
past version could be exploited, so that unpatched instances are not handed a recipe.
