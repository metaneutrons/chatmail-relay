#!/usr/bin/env python3
"""OAuth2 authentication service for chatmail.

Provides web endpoints for OAuth2-based account creation/reset.
Users authenticate with their company OAuth2 provider (Microsoft 365, Google, etc.)
and get a chatmail account created automatically.
"""

import base64
import io
import logging
import os
import secrets
import sys
from pathlib import Path

from flask import Flask, redirect, request, render_template, session, url_for
from authlib.integrations.flask_client import OAuth
from werkzeug.middleware.proxy_fix import ProxyFix
import qrcode

# Import chatmail modules
sys.path.insert(0, str(Path(__file__).parent))
from chatmaild.config import read_config
from chatmaild.doveauth import encrypt_password

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', secrets.token_hex(32))

# Trust proxy headers (nginx sets X-Forwarded-* headers)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# Global config
config = None
oauth = None


def init_oauth2(cfg):
    """Initialize OAuth2 client from config."""
    global oauth
    oauth = OAuth(app)
    
    # Extract tenant ID from authorization endpoint
    tenant_id = cfg.oauth2_authorization_endpoint.split('/')[3]
    
    # Build complete server metadata dict for authlib
    server_metadata = {
        'issuer': f'https://login.microsoftonline.com/{tenant_id}/v2.0',
        'authorization_endpoint': cfg.oauth2_authorization_endpoint,
        'token_endpoint': cfg.oauth2_token_endpoint,
        'jwks_uri': f'https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys',
        'userinfo_endpoint': 'https://graph.microsoft.com/oidc/userinfo',
        'token_endpoint_auth_methods_supported': ['client_secret_post', 'client_secret_basic'],
    }
    
    # Register OAuth2 provider
    oauth.register(
        name='provider',
        client_id=cfg.oauth2_client_id,
        client_secret=cfg.oauth2_client_secret,
        server_metadata_url=None,
        client_kwargs={'scope': 'openid email profile'},
    )
    
    # Manually set the metadata
    oauth.provider._server_metadata = server_metadata


def generate_password(length=24):
    """Generate a secure random password."""
    alphabet = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$%^&*'
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def generate_qr_code(data):
    """Generate QR code as base64 PNG."""
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(data)
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    
    return base64.b64encode(buf.read()).decode('utf-8')


def create_or_reset_account(email, password):
    """Create new account or reset password using chatmail's User class."""
    user = config.get_user(email)
    user.set_password(encrypt_password(password))
    logging.info(f"Created/reset account: {email}")


@app.route('/')
def index():
    """Landing page with OAuth2 login button."""
    if not config.oauth2_enabled:
        return "OAuth2 authentication is not enabled", 503
    
    return render_template('login.html', 
                         provider_name=config.oauth2_provider_name,
                         domain=config.mail_domain)


@app.route('/oauth2-login')
def oauth2_login():
    """Initiate OAuth2 flow."""
    if not config.oauth2_enabled:
        return "OAuth2 authentication is not enabled", 503
    
    redirect_uri = url_for('oauth2_callback', _external=True, _scheme='https')
    return oauth.provider.authorize_redirect(redirect_uri)


@app.route('/oauth2-callback')
def oauth2_callback():
    """Handle OAuth2 callback."""
    try:
        token = oauth.provider.authorize_access_token()
        
        # Extract email from token
        userinfo = token.get('userinfo')
        if not userinfo:
            # Try to get userinfo from separate endpoint
            userinfo = oauth.provider.userinfo()
        
        email = userinfo.get(config.oauth2_email_claim)
        if not email:
            return f"Could not extract email from OAuth2 response (claim: {config.oauth2_email_claim})", 400
        
        # Validate domain
        email_domain = email.split('@')[1] if '@' in email else ''
        if email_domain not in config.oauth2_allowed_domains:
            return f"Email domain '{email_domain}' is not allowed. Allowed domains: {', '.join(config.oauth2_allowed_domains)}", 403
        
        # Extract username (part before @)
        username = email.split('@')[0]
        chatmail_address = f"{username}@{config.mail_domain}"
        
        # Generate random password
        password = generate_password()
        
        # Create or reset account
        create_or_reset_account(chatmail_address, password)
        
        # Generate QR code for DeltaChat
        qr_data = f"DCACCOUNT:{chatmail_address}:{password}"
        qr_code = generate_qr_code(qr_data)
        
        return render_template('success.html',
                             qr_code=qr_code,
                             email=chatmail_address,
                             password=password,
                             domain=config.mail_domain)
    
    except Exception as e:
        logging.error(f"OAuth2 callback error: {e}", exc_info=True)
        return f"Authentication failed: {str(e)}", 500


def main():
    """Run the OAuth2 service."""
    global config
    
    if len(sys.argv) != 2:
        print("Usage: oauth2_service.py <chatmail.ini>")
        sys.exit(1)
    
    config_path = sys.argv[1]
    config = read_config(config_path)
    
    if not config.oauth2_enabled:
        print("OAuth2 is not enabled in config")
        sys.exit(1)
    
    init_oauth2(config)
    
    # Run Flask app
    port = config.oauth2_port
    logging.info(f"Starting OAuth2 service on port {port}")
    app.run(host='127.0.0.1', port=port, debug=False)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    main()
