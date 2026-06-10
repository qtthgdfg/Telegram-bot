#!/usr/bin/env python3
"""
Setup script to generate Telegram session and encode it as base64 for GitHub Secrets.
Run this locally: python setup_session.py
"""

import base64
import os
import sys
from pathlib import Path


def main():
    print("=" * 60)
    print("Telegram Session Setup for CI/CD")
    print("=" * 60)
    print()
    
    session_file = Path("data/telegram_session")
    
    # Check if session file exists
    if not session_file.exists():
        print("❌ Session file not found: data/telegram_session")
        print()
        print("To create a session file:")
        print("1. Run the bot locally: python main.py")
        print("2. Follow the Telegram authentication prompts")
        print("3. Once authenticated, the session file will be created")
        print("4. Run this script again")
        print()
        sys.exit(1)
    
    print(f"✅ Found session file: {session_file}")
    print(f"   Size: {session_file.stat().st_size} bytes")
    print()
    
    # Read and encode to base64
    with open(session_file, 'rb') as f:
        session_data = f.read()
    
    session_b64 = base64.b64encode(session_data).decode('utf-8')
    
    # Save to file
    output_file = Path("TELEGRAM_SESSION_B64.txt")
    with open(output_file, 'w') as f:
        f.write(session_b64)
    
    print("📝 Session encoded to base64")
    print(f"   Base64 length: {len(session_b64)} characters")
    print(f"   Saved to: {output_file}")
    print()
    
    # Display the string (first 100 chars for preview)
    preview = session_b64[:100] + "..." if len(session_b64) > 100 else session_b64
    print("Preview (first 100 chars):")
    print(preview)
    print()
    
    # Instructions
    print("=" * 60)
    print("NEXT STEPS:")
    print("=" * 60)
    print()
    print("1. Open this file: TELEGRAM_SESSION_B64.txt")
    print("2. Copy the entire base64 string")
    print("3. Go to: https://github.com/qtthgdfg/Telegram-bot/settings/secrets/actions")
    print("4. Click 'New repository secret'")
    print("5. Name: TELEGRAM_SESSION_B64")
    print("6. Paste the base64 string in the Value field")
    print("7. Click 'Add secret'")
    print()
    print("After adding the secret, update your workflow to restore the session:")
    print()
    print("- name: Restore Telegram session")
    print("  if: always()")
    print("  run: |")
    print("    if [ ! -z \"${{ secrets.TELEGRAM_SESSION_B64 }}\" ]; then")
    print("      mkdir -p data")
    print("      echo \"${{ secrets.TELEGRAM_SESSION_B64 }}\" | base64 -d > data/telegram_session")
    print("      echo \"✅ Session restored from secret\"")
    print("    fi")
    print()
    print("=" * 60)


if __name__ == "__main__":
    main()
