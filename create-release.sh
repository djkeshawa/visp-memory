#!/bin/bash
# Quick script to create a new release
# This will trigger the GitHub Actions workflow to build and publish

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}╔════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║   Visp Memory - Create Release         ║${NC}"
echo -e "${BLUE}╔════════════════════════════════════════╗${NC}"
echo ""

# Check if we're in a git repo
if ! git rev-parse --git-dir > /dev/null 2>&1; then
    echo -e "${RED}Error: Not in a git repository${NC}"
    exit 1
fi

# Check for uncommitted changes
if ! git diff-index --quiet HEAD --; then
    echo -e "${YELLOW}Warning: You have uncommitted changes${NC}"
    read -p "Continue anyway? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Get current version from pyproject.toml
CURRENT_VERSION=$(grep "^version = " pyproject.toml | sed 's/version = "\(.*\)"/\1/')
echo -e "Current version: ${GREEN}$CURRENT_VERSION${NC}"

# Ask for new version
echo ""
echo "Enter new version number (e.g., 0.1.0, 1.0.0, 0.2.0-beta):"
read -r NEW_VERSION

if [ -z "$NEW_VERSION" ]; then
    echo -e "${RED}Error: Version cannot be empty${NC}"
    exit 1
fi

# Validate version format (basic check)
if ! [[ $NEW_VERSION =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[a-z0-9]+)?$ ]]; then
    echo -e "${YELLOW}Warning: Version format may be invalid (expected: X.Y.Z or X.Y.Z-suffix)${NC}"
    read -p "Continue anyway? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

TAG_NAME="v$NEW_VERSION"

# Check if tag already exists
if git rev-parse "$TAG_NAME" >/dev/null 2>&1; then
    echo -e "${RED}Error: Tag $TAG_NAME already exists${NC}"
    exit 1
fi

echo ""
echo -e "${BLUE}═══════════════════════════════════════${NC}"
echo -e "Creating release: ${GREEN}$TAG_NAME${NC}"
echo -e "${BLUE}═══════════════════════════════════════${NC}"
echo ""

# Update version in pyproject.toml
echo "Updating pyproject.toml..."
sed -i "s/version = \"$CURRENT_VERSION\"/version = \"$NEW_VERSION\"/" pyproject.toml

# Ask for release notes
echo ""
echo "Enter release notes (Ctrl+D when done):"
echo -e "${YELLOW}(Leave empty to auto-generate from commits)${NC}"
RELEASE_NOTES=$(cat)

# Show summary
echo ""
echo -e "${BLUE}═══════════════════════════════════════${NC}"
echo -e "${GREEN}Ready to create release${NC}"
echo -e "${BLUE}═══════════════════════════════════════${NC}"
echo "Version: $NEW_VERSION"
echo "Tag: $TAG_NAME"
echo "Branch: $(git branch --show-current)"
if [ -n "$RELEASE_NOTES" ]; then
    echo "Release notes: (provided)"
else
    echo "Release notes: (auto-generated)"
fi
echo ""
echo "This will:"
echo "  1. Update version in pyproject.toml"
echo "  2. Commit the change"
echo "  3. Create and push tag $TAG_NAME"
echo "  4. Trigger GitHub Actions to build and publish"
echo ""
read -p "Proceed? (y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    # Revert pyproject.toml changes
    git checkout pyproject.toml
    echo -e "${YELLOW}Release cancelled${NC}"
    exit 1
fi

# Commit version bump
echo ""
echo "Committing version bump..."
git add pyproject.toml
git commit -m "chore: bump version to $NEW_VERSION"

# Create and push tag
echo "Creating tag $TAG_NAME..."
if [ -n "$RELEASE_NOTES" ]; then
    git tag -a "$TAG_NAME" -m "$RELEASE_NOTES"
else
    git tag -a "$TAG_NAME" -m "Release $NEW_VERSION"
fi

# Push
echo "Pushing to remote..."
git push origin "$(git branch --show-current)"
git push origin "$TAG_NAME"

echo ""
echo -e "${GREEN}═══════════════════════════════════════${NC}"
echo -e "${GREEN}✓ Release created successfully!${NC}"
echo -e "${GREEN}═══════════════════════════════════════${NC}"
echo ""
echo "Tag $TAG_NAME has been pushed to GitHub."
echo ""
echo "GitHub Actions is now:"
echo "  1. Building the frontend"
echo "  2. Creating the Python wheel"
echo "  3. Creating a GitHub Release"
echo "  4. Uploading the wheel file"
echo ""
echo "Progress: https://github.com/$(git remote get-url origin | sed 's/.*github.com[:/]\(.*\)\.git/\1/')/actions"
echo "Release: https://github.com/$(git remote get-url origin | sed 's/.*github.com[:/]\(.*\)\.git/\1/')/releases/tag/$TAG_NAME"
echo ""
echo -e "${BLUE}Next steps:${NC}"
echo "  1. Wait for GitHub Actions to complete (~3-5 minutes)"
echo "  2. Review the release on GitHub"
echo "  3. (Optional) Publish to PyPI:"
echo "     ${YELLOW}twine upload dist/*.whl${NC}"
echo ""
