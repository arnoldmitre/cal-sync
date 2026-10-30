#!/bin/bash
set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

echo "=== Calendar Sync Setup ==="
echo "1. Checking Python 3..."
if ! command -v python3 &> /dev/null; then
    echo "Error: Python 3 is required but not found."
    exit 1
fi
echo "   Using: $(python3 --version)"

echo "2. Setting up Python virtual environment (venv)..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo "   Created virtual environment at ./venv"
else
    echo "   Virtual environment already exists."
fi

echo "3. Installing dependencies..."
./venv/bin/pip install --upgrade pip --quiet
./venv/bin/pip install -r requirements.txt --quiet
echo "   Dependencies installed successfully."

echo "4. Compiling native macOS EventKit bridge..."
if command -v clang &> /dev/null; then
    clang -O3 -framework Foundation -framework EventKit fetch_events.m -o fetch_events
    echo "   fetch_events binary compiled successfully."
else
    echo "Warning: clang not found. Make sure Xcode Command Line Tools are installed (xcode-select --install)."
fi

echo "5. Initializing configuration & launch agent template..."
if [ ! -f "config.json" ]; then
    cp config.example.json config.json
    echo "   Created config.json from template."
fi

if [ -f "com.user.calendarsync.plist.template" ]; then
    sed "s|__SCRIPT_DIR__|$SCRIPT_DIR|g" com.user.calendarsync.plist.template > com.user.calendarsync.plist
    echo "   Generated com.user.calendarsync.plist for $SCRIPT_DIR"
fi

echo ""
echo "=== Setup Complete! ==="
echo ""
echo "Next steps:"
echo "1. Download your 'credentials.json' from Google Cloud Console and place it in:"
echo "   $SCRIPT_DIR/credentials.json"
echo ""
echo "2. Check your calendar names with:"
echo "   ./venv/bin/python3 sync_calendar.py --list-calendars"
echo ""
echo "3. Run a test dry run with:"
echo "   ./venv/bin/python3 sync_calendar.py --dry-run"
echo ""
echo "4. Perform a real sync with:"
echo "   ./venv/bin/python3 sync_calendar.py --sync"
echo ""
echo "5. Enable background sync (every 30 mins):"
echo "   cp com.user.calendarsync.plist ~/Library/LaunchAgents/"
echo "   launchctl load ~/Library/LaunchAgents/com.user.calendarsync.plist"
echo ""
