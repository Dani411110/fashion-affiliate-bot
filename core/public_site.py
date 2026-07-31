"""Public-facing website served on the same domain as the TikTok integration.

TikTok's app review requires the Website URL to resolve to a real, fully
developed site that explains the service, and it requires the domain shown in
the demo video to match that Website URL. Before this module existed, ``/``
served the internal ops dashboard, so reviewers saw uptime counters and log
tails instead of a website — which is what "Invalid Website URL" and "Website
must be fully developed" in the rejection referred to.

Everything here is public and read-only. The ops dashboard now lives behind
``/admin``.
"""

from __future__ import annotations

import html
import secrets
from urllib.parse import urlencode

from config.settings import Settings

APP_NAME = "Fashion Affiliate Bot"
CONTACT_EMAIL = "lucastanescu28@gmail.com"

_NAV = (
    ("/", "Home"),
    ("/how-it-works", "How it works"),
    ("/faq", "FAQ"),
    ("/support", "Support"),
    ("/connect", "Connect TikTok"),
)

_LAYOUT = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{TITLE}}</title>
  <meta name="description" content="{{DESC}}">
  <style>
    *,*::before,*::after{box-sizing:border-box}
    body,h1,h2,h3,p,ul,ol,li,figure{margin:0}
    body{
      font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;
      background:#0e0f13;color:#f4f4f6;line-height:1.65;
      -webkit-font-smoothing:antialiased;
    }
    a{color:#ff3b5f;text-decoration:none}
    a:hover{text-decoration:underline}
    .wrap{max-width:1040px;margin:0 auto;padding:0 22px}

    nav{border-bottom:1px solid #23252d;background:rgba(14,15,19,.92);position:sticky;top:0;z-index:10}
    nav .wrap{display:flex;align-items:center;gap:26px;min-height:64px;flex-wrap:wrap}
    .brand{display:flex;align-items:center;gap:10px;font-weight:700;font-size:16px;color:#fff}
    .brand:hover{text-decoration:none}
    .dot{width:11px;height:11px;border-radius:50%;background:#ff3b5f;flex:none}
    nav ul{list-style:none;display:flex;gap:20px;padding:0;margin-left:auto;flex-wrap:wrap}
    nav a{color:#b9bbc6;font-size:14.5px}
    nav a:hover{color:#fff;text-decoration:none}

    header.hero{padding:76px 0 56px;border-bottom:1px solid #23252d}
    .eyebrow{color:#ff3b5f;font-size:12.5px;font-weight:700;letter-spacing:.09em;text-transform:uppercase}
    h1{font-size:44px;line-height:1.15;margin:14px 0 18px;letter-spacing:-.02em;max-width:19ch}
    .lede{color:#c2c4ce;font-size:18.5px;max-width:62ch}
    .cta{display:inline-flex;align-items:center;gap:9px;margin-top:30px;padding:13px 22px;
         border-radius:9px;background:#ff3b5f;color:#fff;font-weight:700;font-size:15px}
    .cta:hover{text-decoration:none;filter:brightness(1.08)}
    .cta.ghost{background:transparent;border:1px solid #3a3d47;color:#e8e9ed;margin-left:10px}

    section{padding:56px 0;border-bottom:1px solid #23252d}
    section:last-of-type{border-bottom:0}
    h2{font-size:27px;letter-spacing:-.01em;margin-bottom:10px}
    h3{font-size:18px;margin-bottom:6px;color:#fff}
    .sub{color:#9c9eaa;max-width:66ch;margin-bottom:26px}
    p+p{margin-top:13px}
    .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(248px,1fr));gap:16px}
    .card{border:1px solid #262932;border-radius:11px;background:#161821;padding:20px}
    .card p{color:#b0b2be;font-size:14.5px;margin-top:6px}
    .num{display:inline-flex;align-items:center;justify-content:center;width:27px;height:27px;
         border-radius:7px;background:#ff3b5f1f;color:#ff3b5f;font-weight:700;font-size:13.5px;margin-bottom:11px}

    .steps{list-style:none;counter-reset:s;display:grid;gap:16px}
    .steps li{counter-increment:s;border:1px solid #262932;border-radius:11px;background:#161821;
              padding:20px 20px 20px 62px;position:relative}
    .steps li::before{content:counter(s);position:absolute;left:20px;top:20px;width:28px;height:28px;
              border-radius:8px;background:#ff3b5f;color:#fff;font-weight:700;font-size:14px;
              display:flex;align-items:center;justify-content:center}
    .steps p{color:#b0b2be;font-size:14.5px;margin-top:5px}

    dl.faq{display:grid;gap:0}
    dl.faq div{border-bottom:1px solid #23252d;padding:20px 0}
    dl.faq div:last-child{border-bottom:0}
    dt{font-weight:700;color:#fff;font-size:16.5px;margin-bottom:7px}
    dd{margin:0;color:#b0b2be;font-size:15px}

    .legal{max-width:74ch}
    .legal h2{font-size:20px;margin:34px 0 8px}
    .legal h2:first-of-type{margin-top:8px}
    .legal p,.legal li{color:#bcbec9;font-size:15.3px}
    .legal ul,.legal ol{margin:10px 0 10px 24px}
    .legal li{margin:6px 0}
    .updated{color:#83858f;font-size:13.5px}
    table.scopes{width:100%;border-collapse:collapse;margin-top:16px;font-size:14.5px}
    table.scopes th,table.scopes td{border:1px solid #262932;padding:11px 13px;text-align:left;vertical-align:top}
    table.scopes th{background:#161821;color:#fff;font-size:13px;text-transform:uppercase;letter-spacing:.05em}
    table.scopes td{color:#b0b2be}
    .tablewrap{overflow-x:auto}
    code{background:#1c1f28;border:1px solid #262932;border-radius:5px;padding:1.5px 6px;
         font-size:13.5px;color:#8ff0a8;font-family:ui-monospace,SFMono-Regular,Consolas,monospace}
    .note{border:1px solid #262932;border-left:3px solid #ff3b5f;border-radius:8px;
          background:#161821;padding:16px 18px;margin-top:22px}
    .note p{color:#b0b2be;font-size:14.5px}

    footer{padding:34px 0 46px;color:#83858f;font-size:14px}
    footer .wrap{display:flex;gap:18px;flex-wrap:wrap;align-items:center}
    footer a{color:#a8aab5}
    footer .sep{margin-left:auto}

    @media (max-width:640px){
      h1{font-size:33px}
      header.hero{padding:52px 0 42px}
      nav ul{gap:14px}
      .cta.ghost{margin-left:0;margin-top:10px}
    }
  </style>
</head>
<body>
  <nav>
    <div class="wrap">
      <a class="brand" href="/"><span class="dot"></span>{{APP}}</a>
      <ul>{{NAV}}</ul>
    </div>
  </nav>
  {{BODY}}
  <footer>
    <div class="wrap">
      <span>&copy; 2026 {{APP}}</span>
      <a href="/privacy">Privacy Policy</a>
      <a href="/terms">Terms of Service</a>
      <a href="/support">Support</a>
      <span class="sep"><a href="mailto:{{EMAIL}}">{{EMAIL}}</a></span>
    </div>
  </footer>
</body>
</html>"""


def _page(title: str, description: str, body: str) -> str:
    nav = "".join(
        f'<li><a href="{href}">{html.escape(label)}</a></li>' for href, label in _NAV
    )
    return (
        _LAYOUT.replace("{{TITLE}}", html.escape(title))
        .replace("{{DESC}}", html.escape(description))
        .replace("{{NAV}}", nav)
        .replace("{{BODY}}", body)
        .replace("{{APP}}", APP_NAME)
        .replace("{{EMAIL}}", CONTACT_EMAIL)
    )


# ── Landing ──────────────────────────────────────────────────────────────────

def landing_html() -> str:
    body = """
  <header class="hero">
    <div class="wrap">
      <p class="eyebrow">Fashion affiliate publishing, with a human in the loop</p>
      <h1>Prepare fashion posts once. Approve them. Publish.</h1>
      <p class="lede">
        Fashion Affiliate Bot assembles fashion affiliate posts &mdash; product photos, outfit
        pairings, captions and affiliate links &mdash; and holds every one of them for the
        operator's approval before anything is published to their own connected TikTok profile.
        Nothing is ever posted automatically.
      </p>
      <a class="cta" href="/connect">Connect your TikTok account</a>
      <a class="cta ghost" href="/how-it-works">See how it works</a>
    </div>
  </header>

  <section>
    <div class="wrap">
      <h2>What this service does</h2>
      <p class="sub">
        It is a publishing assistant for a single fashion affiliate operator. It handles the
        repetitive part &mdash; collecting product imagery, writing a first-draft caption,
        attaching the right affiliate link, sizing media for the platform &mdash; and then stops
        and waits for a person to decide.
      </p>
      <div class="grid">
        <div class="card">
          <div class="num">1</div>
          <h3>Curated media sets</h3>
          <p>Product photography and outfit inspiration are grouped into coherent posts rather than
             posted one image at a time.</p>
        </div>
        <div class="card">
          <div class="num">2</div>
          <h3>Draft captions</h3>
          <p>A first-draft caption and hashtag set is written for each post. The operator edits or
             rewrites it before approving.</p>
        </div>
        <div class="card">
          <div class="num">3</div>
          <h3>Affiliate links and disclosure</h3>
          <p>The correct affiliate link is attached to each product, so the operator can add the
             disclosure their market requires.</p>
        </div>
        <div class="card">
          <div class="num">4</div>
          <h3>Explicit approval gate</h3>
          <p>Every post is previewed and must be approved, rejected or regenerated by the operator.
             There is no unattended posting path.</p>
        </div>
        <div class="card">
          <div class="num">5</div>
          <h3>Platform-correct media</h3>
          <p>Images are resized and padded for each destination so posts are not cropped or
             rejected by the platform.</p>
        </div>
        <div class="card">
          <div class="num">6</div>
          <h3>Publishing history</h3>
          <p>What was published, when, and to which account is recorded so the operator can audit
             their own activity.</p>
        </div>
      </div>
    </div>
  </section>

  <section>
    <div class="wrap">
      <h2>How TikTok is used</h2>
      <p class="sub">
        The service integrates with TikTok for exactly two things: identifying which account is
        connected, and publishing content the operator has approved. It does not read anyone
        else's data.
      </p>
      <div class="tablewrap">
      <table class="scopes">
        <tr><th>Product</th><th>Scope</th><th>What it is used for</th></tr>
        <tr>
          <td>Login Kit</td>
          <td><code>user.info.basic</code></td>
          <td>Reads the connected account's display name and avatar, so the operator can confirm
              on screen which TikTok profile is about to be posted to. Nothing else is read.</td>
        </tr>
        <tr>
          <td>Content Posting API</td>
          <td><code>video.publish</code></td>
          <td>Publishes a post to the connected profile &mdash; only after the operator has pressed
              approve on that specific post.</td>
        </tr>
      </table>
      </div>
      <div class="note">
        <p>The operator can disconnect at any time from TikTok &rarr; Settings &rarr; Security and
           permissions &rarr; Manage app permissions. Revoking access stops all publishing
           immediately and invalidates the stored tokens.</p>
      </div>
    </div>
  </section>

  <section>
    <div class="wrap">
      <h2>Get started</h2>
      <p class="sub">Connecting takes one authorization step, and can be undone at any time.</p>
      <a class="cta" href="/connect">Connect your TikTok account</a>
    </div>
  </section>
"""
    return _page(
        f"{APP_NAME} — fashion affiliate publishing with human approval",
        "Fashion Affiliate Bot prepares fashion affiliate posts and publishes them to the "
        "operator's own connected TikTok profile only after explicit approval.",
        body,
    )


# ── How it works ─────────────────────────────────────────────────────────────

def how_it_works_html() -> str:
    body = """
  <header class="hero">
    <div class="wrap">
      <p class="eyebrow">How it works</p>
      <h1>Five steps, one of them human</h1>
      <p class="lede">
        The full path a post takes, from raw product data to a published TikTok post. Step 4 is a
        person, and it cannot be skipped or automated away.
      </p>
    </div>
  </header>

  <section>
    <div class="wrap">
      <ol class="steps">
        <li>
          <h3>Connect the account</h3>
          <p>The operator authorizes their own TikTok account through TikTok's standard OAuth
             screen. The service stores only the resulting access and refresh tokens, and reads
             the display name and avatar so the connected profile is visible on screen.</p>
        </li>
        <li>
          <h3>Collect products and imagery</h3>
          <p>Product details and photography are gathered for the categories the operator has
             chosen, then de-duplicated against what has already been posted so the same item is
             not published twice.</p>
        </li>
        <li>
          <h3>Assemble a draft</h3>
          <p>Images are grouped into a single coherent post, resized and padded to the aspect ratio
             the platform expects. A draft caption and hashtag set is written, and the matching
             affiliate link is attached to each product.</p>
        </li>
        <li>
          <h3>Human review &mdash; required</h3>
          <p>The complete draft is presented to the operator: every image, the caption, the
             hashtags and the links. The operator approves it, rejects it, or asks for it to be
             regenerated. A draft that is never approved is never published. There is no timeout
             that auto-approves and no scheduled path that bypasses this step.</p>
        </li>
        <li>
          <h3>Publish and record</h3>
          <p>Only on approval is the post sent to the connected profile through TikTok's Content
             Posting API, using the <code>video.publish</code> scope. The result is written to the
             operator's own publishing history.</p>
        </li>
      </ol>
      <div class="note">
        <p><strong>What the service never does:</strong> it does not read the operator's videos,
           followers, messages or analytics; it does not access any other TikTok user's data; it
           does not post on behalf of anyone but the connected operator; and it does not publish
           anything the operator has not explicitly approved.</p>
      </div>
    </div>
  </section>
"""
    return _page(
        f"How it works — {APP_NAME}",
        "The five steps a post takes through Fashion Affiliate Bot, including the required "
        "human approval gate before anything is published to TikTok.",
        body,
    )


# ── FAQ ──────────────────────────────────────────────────────────────────────

def faq_html() -> str:
    body = """
  <header class="hero">
    <div class="wrap">
      <p class="eyebrow">FAQ</p>
      <h1>Questions about the service</h1>
      <p class="lede">If something is not covered here, write to us and we will answer.</p>
    </div>
  </header>

  <section>
    <div class="wrap">
      <dl class="faq">
        <div>
          <dt>Who is this service for?</dt>
          <dd>A fashion affiliate operator who publishes product and outfit content to their own
              social profiles and wants the assembly work handled while keeping the final
              publishing decision.</dd>
        </div>
        <div>
          <dt>Will it post to my account without asking?</dt>
          <dd>No. Every post must be approved by you before it is published. There is no
              unattended publishing mode, no auto-approve timeout, and no scheduled job that can
              publish an unapproved draft.</dd>
        </div>
        <div>
          <dt>What TikTok permissions do you request, and why?</dt>
          <dd>Two. <code>user.info.basic</code> reads your display name and avatar so you can see
              which account is connected before publishing. <code>video.publish</code> publishes a
              post you have approved. We request nothing else.</dd>
        </div>
        <div>
          <dt>Can you read my videos, followers, messages or analytics?</dt>
          <dd>No. Those permissions are not requested and that data is never accessed.</dd>
        </div>
        <div>
          <dt>Do you touch other people's TikTok accounts?</dt>
          <dd>No. The only account the service can act on is the one whose owner completed the
              authorization themselves.</dd>
        </div>
        <div>
          <dt>How do I disconnect?</dt>
          <dd>In TikTok, go to Settings &rarr; Security and permissions &rarr; Manage app
              permissions and remove the app. Publishing stops immediately and the stored tokens
              stop working. You can also email us and we will delete them from our side.</dd>
        </div>
        <div>
          <dt>Where is my data stored, and for how long?</dt>
          <dd>Access and refresh tokens are held as protected secrets on the hosting provider for
              as long as the account stays connected, and are deleted when you disconnect. Your
              own drafts and publishing history are kept for your reference and can be deleted on
              request. See the <a href="/privacy">Privacy Policy</a>.</dd>
        </div>
        <div>
          <dt>Are the captions written by a human?</dt>
          <dd>The first draft is generated automatically from the product information. You read it,
              edit it if you want, and it is only published if you approve it.</dd>
        </div>
        <div>
          <dt>Who is responsible for affiliate disclosure?</dt>
          <dd>You are. Posts may contain affiliate links, and you are responsible for including the
              disclosure your jurisdiction and TikTok's policies require. See the
              <a href="/terms">Terms of Service</a>.</dd>
        </div>
        <div>
          <dt>Is this service affiliated with TikTok?</dt>
          <dd>No. It is an independent tool that uses TikTok's public developer APIs. TikTok is a
              trademark of its owner.</dd>
        </div>
      </dl>
    </div>
  </section>
"""
    return _page(
        f"FAQ — {APP_NAME}",
        "Answers about permissions, approval, data storage and disconnecting from "
        "Fashion Affiliate Bot.",
        body,
    )


# ── Support ──────────────────────────────────────────────────────────────────

def support_html() -> str:
    body = """
  <header class="hero">
    <div class="wrap">
      <p class="eyebrow">Support</p>
      <h1>Get help or delete your data</h1>
      <p class="lede">
        One person reads this inbox. Expect a reply within three business days.
      </p>
      <a class="cta" href="mailto:{{EMAIL}}">Email support</a>
    </div>
  </header>

  <section>
    <div class="wrap">
      <h2>Contact</h2>
      <p class="sub">
        Email <a href="mailto:{{EMAIL}}">{{EMAIL}}</a> for anything: a problem with publishing, a
        question about permissions, a privacy request, or a report of misuse.
      </p>

      <div class="grid">
        <div class="card">
          <h3>Revoke access</h3>
          <p>TikTok &rarr; Settings &rarr; Security and permissions &rarr; Manage app permissions
             &rarr; remove the app. This takes effect immediately and needs no action from us.</p>
        </div>
        <div class="card">
          <h3>Delete your data</h3>
          <p>Email us from the address associated with your account and ask for deletion. We remove
             stored tokens, drafts and publishing history and confirm when it is done.</p>
        </div>
        <div class="card">
          <h3>Report a problem with a post</h3>
          <p>Send the post link and what is wrong with it. If content was published in error we
             will help you remove it and investigate how it got past approval.</p>
        </div>
        <div class="card">
          <h3>Security reports</h3>
          <p>If you believe you have found a vulnerability, email us with the details and do not
             disclose it publicly until it is fixed.</p>
        </div>
      </div>
    </div>
  </section>
"""
    return _page(
        f"Support — {APP_NAME}",
        "Contact support, revoke access, or request deletion of your Fashion Affiliate Bot data.",
        body.replace("{{EMAIL}}", CONTACT_EMAIL),
    )


# ── Connect ──────────────────────────────────────────────────────────────────

def _redirect_uri(settings: Settings, request_host: str | None) -> str:
    """Where TikTok should send the operator back after authorization.

    Derived from the host the request came in on, so renaming the deployment's
    domain cannot silently produce an authorize URL pointing at a domain that no
    longer resolves. TIKTOK_REDIRECT_URI still wins when set, because the value
    registered in the TikTok app must match this exactly.
    """
    configured = (settings.tiktok_redirect_uri or "").strip()
    if configured:
        return configured
    if request_host:
        return f"https://{request_host}/tiktok/callback"
    return ""


def connect_html(settings: Settings, request_host: str | None = None) -> str:
    connected = bool(settings.tiktok_access_token)
    redirect_uri = _redirect_uri(settings, request_host)

    if not settings.tiktok_client_key or not redirect_uri:
        action = (
            '<div class="note"><p>The TikTok integration is not configured on this deployment '
            "yet, so the authorization button is unavailable. If you are the operator, set "
            "<code>TIKTOK_CLIENT_KEY</code> and redeploy.</p></div>"
        )
    else:
        params = {
            "client_key": settings.tiktok_client_key,
            "scope": "user.info.basic,video.publish",
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "state": secrets.token_urlsafe(16),
        }
        auth_url = "https://www.tiktok.com/v2/auth/authorize/?" + urlencode(params)
        action = (
            f'<a class="cta" href="{html.escape(auth_url)}">Continue with TikTok</a>'
            '<a class="cta ghost" href="/how-it-works">What will it be allowed to do?</a>'
        )

    status = (
        '<div class="note"><p><strong>An account is currently connected.</strong> '
        "Authorizing again replaces the existing connection.</p></div>"
        if connected
        else '<div class="note"><p>No TikTok account is connected to this deployment yet.</p></div>'
    )

    body = """
  <header class="hero">
    <div class="wrap">
      <p class="eyebrow">Connect</p>
      <h1>Authorize your own TikTok account</h1>
      <p class="lede">
        You will be taken to TikTok's own authorization screen. Nothing is published as a result of
        connecting &mdash; posts still require your approval, one at a time.
      </p>
      {{ACTION}}
    </div>
  </header>

  <section>
    <div class="wrap">
      <h2>What you are granting</h2>
      <p class="sub">Two permissions, and nothing beyond them.</p>
      <div class="tablewrap">
      <table class="scopes">
        <tr><th>Permission</th><th>What it allows</th></tr>
        <tr>
          <td><code>user.info.basic</code></td>
          <td>Read your display name and avatar, so the connected account is shown on screen before
              anything is published.</td>
        </tr>
        <tr>
          <td><code>video.publish</code></td>
          <td>Publish a post to your profile &mdash; only one you have explicitly approved.</td>
        </tr>
      </table>
      </div>
      {{STATUS}}
      <p style="margin-top:22px">
        You can withdraw both at any time from TikTok &rarr; Settings &rarr; Security and
        permissions &rarr; Manage app permissions. See the <a href="/privacy">Privacy Policy</a>
        and <a href="/terms">Terms of Service</a>.
      </p>
    </div>
  </section>
"""
    return _page(
        f"Connect TikTok — {APP_NAME}",
        "Authorize Fashion Affiliate Bot to publish approved posts to your own TikTok profile.",
        body.replace("{{ACTION}}", action).replace("{{STATUS}}", status),
    )


# ── Legal ────────────────────────────────────────────────────────────────────

_UPDATED = "26 July 2026"


def privacy_html() -> str:
    body = """
  <header class="hero">
    <div class="wrap">
      <p class="eyebrow">Legal</p>
      <h1>Privacy Policy</h1>
      <p class="updated">Last updated: {{UPDATED}}</p>
    </div>
  </header>

  <section>
    <div class="wrap legal">
      <p>This Privacy Policy explains what information {{APP}} ("the service", "we", "us")
         accesses, why it accesses it, how long it is kept, and how you can have it deleted. It
         applies to the operator who connects their own social account to the service. The service
         is a publishing assistant used by its operator; it is not a consumer social product and it
         does not collect information about the audience who sees the published posts.</p>

      <h2>1. Who this policy covers</h2>
      <p>The service is used by its operator, acting on their own accounts. The only TikTok account
         it can act on is one whose owner completed TikTok's authorization flow themselves. We do
         not access, request, or store information belonging to any other TikTok user, and we do
         not build profiles of viewers, followers, or commenters.</p>

      <h2>2. What we access, and the purpose of each item</h2>
      <ul>
        <li><strong>Basic profile information</strong> (TikTok scope <code>user.info.basic</code>)
            &mdash; your display name, avatar and account identifier. Purpose: to show you which
            account is connected before a post is published, so you cannot publish to the wrong
            profile.</li>
        <li><strong>Publishing permission</strong> (TikTok scope <code>video.publish</code>)
            &mdash; used to transmit a post you have approved to your profile. Purpose: publishing.
            No content is transmitted without your prior approval of that specific post.</li>
        <li><strong>Authorization tokens</strong> &mdash; the access and refresh tokens TikTok
            issues when you connect. Purpose: to keep the authorized session valid so you do not
            have to reconnect before every post.</li>
        <li><strong>Content you create in the service</strong> &mdash; the product images, captions,
            hashtags and affiliate links that make up your drafts, plus a record of what was
            published and when. Purpose: to let you review drafts and audit your own publishing
            history.</li>
        <li><strong>Operational logs</strong> &mdash; timestamps, error messages and outcomes of
            publishing attempts. Purpose: to diagnose failures. Logs are scrubbed of credentials.</li>
      </ul>
      <p>We do not access your videos, followers, direct messages, comments, watch history or
         analytics. Those permissions are not requested.</p>

      <h2>3. What we do not do with your information</h2>
      <ul>
        <li>We do not sell personal data, and we never have.</li>
        <li>We do not share it with third parties for their own purposes.</li>
        <li>We do not use it for advertising, ad targeting, profiling, or scoring.</li>
        <li>We do not use TikTok data to train machine learning models.</li>
        <li>We do not combine it with data bought from data brokers.</li>
      </ul>

      <h2>4. Service providers</h2>
      <p>A small number of providers process data strictly to perform a stated function, under
         their own terms:</p>
      <ul>
        <li><strong>TikTok for Developers</strong> &mdash; authorization and publishing of approved
            content to your account.</li>
        <li><strong>The hosting provider</strong> &mdash; runs the service and stores its database
            and secrets.</li>
        <li><strong>A large-language-model provider</strong> &mdash; receives product descriptions
            in order to draft captions. Your TikTok tokens and profile information are never sent
            to it.</li>
        <li><strong>Cloud file storage</strong> &mdash; holds the images used in your posts so they
            can be transmitted at publish time.</li>
      </ul>
      <p>We do not authorize any of these providers to use your information for their own
         independent purposes.</p>

      <h2>5. Storage and security</h2>
      <p>Authorization tokens are held as protected environment secrets on the hosting provider,
         are never rendered in a public page, and are redacted from logs. Drafts and publishing
         history are stored in a private database on a persistent volume. Access is limited to the
         operator. Traffic between you, the service and TikTok is encrypted in transit over HTTPS.</p>
      <p>No system is perfectly secure. If a breach affects your information, we will tell you at
         the contact address associated with your account and describe what happened and what we
         are doing about it.</p>

      <h2>6. Retention</h2>
      <ul>
        <li><strong>Authorization tokens</strong> &mdash; kept while the account remains connected;
            deleted when you disconnect or revoke access, or on request.</li>
        <li><strong>Drafts and publishing history</strong> &mdash; kept for your own reference until
            you delete them or ask us to.</li>
        <li><strong>Operational logs</strong> &mdash; kept short-term for diagnostics, then rotated
            out.</li>
      </ul>

      <h2>7. Your rights and choices</h2>
      <p>You can, at any time:</p>
      <ul>
        <li><strong>Revoke access</strong> &mdash; TikTok &rarr; Settings &rarr; Security and
            permissions &rarr; Manage app permissions. This takes effect immediately, stops all
            publishing, and invalidates the stored tokens. It requires no action from us.</li>
        <li><strong>Request access, correction, export or deletion</strong> of the information the
            service holds about you, by emailing
            <a href="mailto:{{EMAIL}}">{{EMAIL}}</a>. We respond within 30 days.</li>
        <li><strong>Withdraw consent</strong> by disconnecting; this does not affect posts already
            published.</li>
      </ul>
      <p>Depending on where you live, you may have additional statutory rights, including under the
         EU/UK GDPR and comparable laws. We honour those requests through the same contact
         address.</p>

      <h2>8. International transfers</h2>
      <p>The service and its providers may process data in countries other than yours, including
         the United States. Where required, transfers rely on appropriate safeguards such as the
         European Commission's standard contractual clauses.</p>

      <h2>9. Children</h2>
      <p>The service is not directed to children and must not be used by anyone below the minimum
         age required by TikTok's own Terms of Service. We do not knowingly process information
         about children. If we learn that we have, we delete it.</p>

      <h2>10. Changes to this policy</h2>
      <p>We may update this policy. Material changes will be reflected here with a new "last
         updated" date, and where the change is significant we will notify the connected operator
         directly.</p>

      <h2>11. Contact</h2>
      <p>Questions or requests: <a href="mailto:{{EMAIL}}">{{EMAIL}}</a>. This address is the
         contact point for privacy matters, including deletion requests.</p>
    </div>
  </section>
"""
    return _page(
        f"Privacy Policy — {APP_NAME}",
        "What Fashion Affiliate Bot accesses, why, how long it is kept, and how to have it "
        "deleted.",
        body.replace("{{UPDATED}}", _UPDATED)
        .replace("{{EMAIL}}", CONTACT_EMAIL)
        .replace("{{APP}}", APP_NAME),
    )


def terms_html() -> str:
    body = """
  <header class="hero">
    <div class="wrap">
      <p class="eyebrow">Legal</p>
      <h1>Terms of Service</h1>
      <p class="updated">Last updated: {{UPDATED}}</p>
    </div>
  </header>

  <section>
    <div class="wrap legal">
      <p>These Terms of Service ("Terms") are a binding agreement between {{APP}} ("the service",
         "we", "us") and the person who connects an account to it ("you", "the operator"). By
         connecting an account or using the service you accept these Terms. If you do not accept
         them, do not connect an account.</p>

      <h2>1. What the service does</h2>
      <p>The service assembles fashion affiliate posts &mdash; product images, captions, hashtags
         and affiliate links &mdash; presents each one to you for review, and publishes only those
         you approve to your own connected social profile, using the platform's official APIs. It
         is a publishing assistant; it does not act independently of your approval.</p>

      <h2>2. Eligibility</h2>
      <p>You must be old enough to hold an account on each platform you connect, must hold that
         account legitimately, and must have authority to publish to it. You are responsible for
         keeping your credentials and your device secure.</p>

      <h2>3. Your account and connected platforms</h2>
      <p>You connect your own accounts through each platform's authorization flow. You may
         disconnect at any time from the platform's own settings. You remain bound by each
         connected platform's terms, including
         <a href="https://www.tiktok.com/legal/terms-of-service" target="_blank" rel="noopener">TikTok's
         Terms of Service</a> and its
         <a href="https://www.tiktok.com/community-guidelines" target="_blank" rel="noopener">Community
         Guidelines</a>. Nothing in these Terms overrides them; where they conflict, the platform's
         rules govern your use of that platform.</p>

      <h2>4. Approval is yours, and so is the content</h2>
      <p>Every post requires your explicit approval before publication. Once you approve a post,
         you are its publisher. You are responsible for:</p>
      <ul>
        <li>holding the rights to every image, product photograph, trademark and description in it;</li>
        <li>the accuracy of prices, availability, product claims and links;</li>
        <li>compliance with advertising law and platform policy in your market;</li>
        <li>including the affiliate and paid-partnership disclosures required of you.</li>
      </ul>

      <h2>5. Affiliate links and disclosure</h2>
      <p>Posts may contain affiliate links from which you may earn a commission. Disclosing that
         relationship is your legal obligation, not ours. Many jurisdictions and TikTok itself
         require a clear, conspicuous disclosure; failing to include one can expose you to
         regulatory action and to enforcement by the platform.</p>

      <h2>6. Acceptable use</h2>
      <p>You must not use the service to:</p>
      <ul>
        <li>publish spam, bulk repetitive content, or engagement-manipulation content;</li>
        <li>publish counterfeit goods, or infringe anyone's copyright or trademark;</li>
        <li>publish misleading, deceptive, or unlawful content;</li>
        <li>impersonate any person or organization;</li>
        <li>circumvent a platform's rate limits, review processes, or technical restrictions;</li>
        <li>operate accounts you do not own, or resell access to the service;</li>
        <li>attempt to gain unauthorized access to the service or to other users' data.</li>
      </ul>
      <p>We may suspend or terminate your use of the service immediately if it is used this way.</p>

      <h2>7. Intellectual property</h2>
      <p>You keep all rights in your own content; we claim no ownership of it. You grant us only
         the limited permission needed to process and transmit that content in order to publish it
         at your direction. The service's own software and design remain ours. TikTok and all other
         platform names and logos belong to their respective owners; the service is independent and
         is not affiliated with, endorsed by, or sponsored by TikTok.</p>

      <h2>8. Availability</h2>
      <p>The service is provided "as is" and "as available", without warranties of any kind, express
         or implied, including merchantability, fitness for a particular purpose, and
         non-infringement. We do not guarantee uninterrupted operation, and publishing may fail or
         be delayed because of platform API changes, rate limits, outages, review decisions, or
         account restrictions outside our control. We may modify, suspend or discontinue the
         service.</p>

      <h2>9. Limitation of liability</h2>
      <p>To the maximum extent permitted by law, we are not liable for indirect, incidental,
         special, consequential or punitive damages, nor for lost profits, lost revenue, lost
         commissions, or lost data. This includes any action a platform takes in response to
         content you approved &mdash; removal of a post, loss of reach, restriction, suspension or
         termination of your account. Our total aggregate liability is limited to the greater of the
         amount you paid us in the twelve months before the claim, or 50 EUR. Some jurisdictions do
         not allow these exclusions, in which case they apply only to the extent permitted.</p>

      <h2>10. Indemnity</h2>
      <p>You will indemnify us against claims, damages and reasonable costs arising from content you
         approved for publication, from your breach of these Terms, or from your breach of a
         connected platform's rules or of applicable law.</p>

      <h2>11. Termination</h2>
      <p>You may stop using the service at any time and revoke its access from your platform
         settings. We may suspend or terminate access for breach of these Terms or of a platform's
         policies. On termination we delete stored authorization tokens; you may request deletion of
         your remaining data as described in the <a href="/privacy">Privacy Policy</a>.</p>

      <h2>12. Changes to these Terms</h2>
      <p>We may update these Terms. Material changes will be posted here with a new "last updated"
         date. Continuing to use the service after a change means you accept the updated Terms.</p>

      <h2>13. Governing law</h2>
      <p>These Terms are governed by the law of the operator's country of residence, without regard
         to its conflict-of-laws rules, and nothing here limits any consumer rights you have that
         cannot be waived by agreement.</p>

      <h2>14. Contact</h2>
      <p>Questions about these Terms: <a href="mailto:{{EMAIL}}">{{EMAIL}}</a>.</p>
    </div>
  </section>
"""
    return _page(
        f"Terms of Service — {APP_NAME}",
        "The terms governing use of Fashion Affiliate Bot, including your responsibility for "
        "approved content and affiliate disclosure.",
        body.replace("{{UPDATED}}", _UPDATED)
        .replace("{{EMAIL}}", CONTACT_EMAIL)
        .replace("{{APP}}", APP_NAME),
    )
