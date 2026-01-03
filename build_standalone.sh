#!/bin/bash
# Build standalone executable for Visp Memory

set -e

echo "================================"
echo "Visp Memory Standalone Builder"
echo "================================"

# Check if PyInstaller is installed
if ! command -v pyinstaller &> /dev/null; then
    echo "Installing PyInstaller..."
    pip install pyinstaller
fi

# Build frontend first
echo ""
echo "Step 1: Building frontend..."
python build_frontend.py

# Build standalone executable
echo ""
echo "Step 2: Building standalone executable..."
pyinstaller visp-memory.spec

# Create distribution directory
echo ""
echo "Step 3: Creating distribution package..."
DIST_DIR="dist/visp-memory-standalone"
mkdir -p "$DIST_DIR"

# Copy executable
cp dist/visp-memory "$DIST_DIR/"

# Copy documentation
cp README.md "$DIST_DIR/"
cp LICENSE "$DIST_DIR/"

# Create startup script
cat > "$DIST_DIR/start-server.sh" << 'EOF'
#!/bin/bash
# Start Visp Memory server with dashboard

echo "Starting Visp Memory Server..."
echo "Dashboard will be available at: http://localhost:8000/dashboard"
echo "API docs at: http://localhost:8000/docs"
echo ""

# Initialize memory if needed
if [ ! -d "$HOME/.visp-memory" ]; then
    echo "Initializing Visp Memory..."
    ./visp-memory init --type code
fi

# Start server
./visp-memory serve --port 8000
EOF

chmod +x "$DIST_DIR/start-server.sh"

# Create README for the distribution
cat > "$DIST_DIR/README-STANDALONE.md" << 'EOF'
# Visp Memory Standalone Distribution

This is a standalone distribution of Visp Memory with an embedded dashboard.

## Quick Start

1. Run the server:
   ```bash
   ./start-server.sh
   ```

2. Open your browser to http://localhost:8000/dashboard

3. Use the CLI:
   ```bash
   ./visp-memory --help
   ```

## What's Included

- Visp Memory CLI tool
- FastAPI server
- Web dashboard (embedded)
- All required Python dependencies

## System Requirements

- 64-bit Linux/macOS/Windows
- No Python installation required
- Minimum 2GB RAM
- 500MB disk space

## Data Storage

Data is stored in `~/.visp-memory/` by default.

## Configuration

Edit `~/.visp-memory/config.yaml` to customize settings.

For full documentation, visit: https://github.com/yourusername/visp-memory
EOF

# Create archive
echo ""
echo "Step 4: Creating archive..."
cd dist
tar -czf visp-memory-standalone-$(uname -s)-$(uname -m).tar.gz visp-memory-standalone/
cd ..

echo ""
echo "================================"
echo "Build complete!"
echo "================================"
echo "Distribution: dist/visp-memory-standalone-$(uname -s)-$(uname -m).tar.gz"
echo ""
echo "To test:"
echo "  cd dist/visp-memory-standalone"
echo "  ./visp-memory --help"
