#!/usr/bin/env python3
"""
Garmin Local Auth Helper
========================
Run this script on your LOCAL MACHINE (home/office network — NOT the server) to
authenticate with Garmin Connect and export an OAuth token that can be uploaded
to the server web UI.

Usage:
    python scripts/garmin_auth_local.py

Requirements:
    pip install garminconnect
"""

import getpass
import sys


def main():
    try:
        from garminconnect import Garmin
    except ImportError:
        print("ERROR: garminconnect not installed.")
        print("Run: pip install garminconnect")
        sys.exit(1)

    print("=" * 60)
    print("Garmin Connect — Local Authentication")
    print("=" * 60)
    print("This script must run on your LOCAL machine (home/office IP).")
    print("Server IPs are blocked by Garmin's anti-bot protection.")
    print()

    email = input("Garmin email: ").strip()
    if not email:
        print("ERROR: email is required")
        sys.exit(1)

    password = getpass.getpass("Garmin password: ")
    if not password:
        print("ERROR: password is required")
        sys.exit(1)

    print()
    print("Authenticating with Garmin Connect...")

    try:
        g = Garmin(email, password)
        mfa_code, _ = g.login()

        if mfa_code == "needs_mfa":
            print("MFA required — check your email/phone for the code.")
            mfa = input("Enter MFA code: ").strip()
            g.resume_login({}, mfa)

        # Verify it worked
        name = g.get_full_name()
        print(f"✅ Authenticated as: {name}")

    except Exception as e:
        print(f"ERROR: Authentication failed: {e}")
        print()
        print("Common causes:")
        print("  - Wrong email or password")
        print("  - MFA required but not handled")
        print("  - Garmin servers temporarily unavailable")
        sys.exit(1)

    # Export the OAuth token
    try:
        token_json = g.client.dumps()
    except Exception as e:
        print(f"ERROR: Could not export token: {e}")
        sys.exit(1)

    print()
    print("=" * 60)
    print("TOKEN (copy the entire block below):")
    print("=" * 60)
    print(token_json)
    print("=" * 60)
    print()
    print("Next steps:")
    print("1. Copy the token JSON above")
    print("2. Open the web console → Setup tab → Garmin Connect")
    print("3. Paste into 'Upload Token' textarea → click Upload")
    print()
    print("The token works from any IP. It auto-refreshes on the server.")


if __name__ == "__main__":
    main()
