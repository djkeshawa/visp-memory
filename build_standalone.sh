#!/bin/bash
# Build standalone executable for LLM Memory

set -e

echo "================================"
echo "LLM Memory Standalone Builder"
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
pyinstaller llm-memory.spec

# Create distribution directory
echo ""
echo "Step 3: Creating distribution package..."
DIST_DIR="dist/llm-memory-standalone"
mkdir -p "$DIST_DIR"

# Copy executable
cp dist/llm-memory "$DIST_DIR/"

# Copy documentation
cp README.md "$DIST_DIR/"
cp LICENSE "$DIST_DIR/"

# Create startup script
cat > "$DIST_DIR/start-server.sh" << 'EOF'
#!/bin/bash
# Start LLM Memory server with dashboard

echo "Starting LLM Memory Server..."
echo "Dashboard will be available at: http://localhost:8000/dashboard"
echo "API docs at: http://localhost:8000/docs"
echo ""

# Initialize memory if needed
if [ ! -d "$HOME/.llm-memory" ]; then
    echo "Initializing LLM Memory..."
    ./llm-memory init --type code
fi

# Start server
./llm-memory server --port 8000
EOF

chmod +x "$DIST_DIR/start-server.sh"

# Create README for the distribution
cat > "$DIST_DIR/README-STANDALONE.md" << 'EOF'
# LLM Memory Standalone Distribution

This is a standalone distribution of LLM Memory with an embedded dashboard.

## Quick Start

1. Run the server:
   ```bash
   ./start-server.sh
   ```

2. Open your browser to http://localhost:8000/dashboard

3. Use the CLI:
   ```bash
   ./llm-memory --help
   ```

## What's Included

- LLM Memory CLI tool
- FastAPI server
- Web dashboard (embedded)
- All required Python dependencies

## System Requirements

- 64-bit Linux/macOS/Windows
- No Python installation required
- Minimum 2GB RAM
- 500MB disk space

## Data Storage

Data is stored in `~/.llm-memory/` by default.

## Configuration

Edit `~/.llm-memory/config.yaml` to customize settings.

For full documentation, visit: https://github.com/yourusername/llm-memory
EOF

# Create archive
echo ""
echo "Step 4: Creating archive..."
cd dist
tar -czf llm-memory-standalone-$(uname -s)-$(uname -m).tar.gz llm-memory-standalone/
cd ..

echo ""
echo "================================"
echo "Build complete!"
echo "================================"
echo "Distribution: dist/llm-memory-standalone-$(uname -s)-$(uname -m).tar.gz"
echo ""
echo "To test:"
echo "  cd dist/llm-memory-standalone"
echo "  ./llm-memory --help"
