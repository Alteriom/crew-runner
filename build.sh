#!/bin/bash
# Build crew-runner Docker image with bundled CrewAI skills

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS_SOURCE="$HOME/alteriom-claude-skills/crewai-skills"
BUILD_CONTEXT="$SCRIPT_DIR"

echo "🏗️  Building crew-runner with CrewAI skills..."
echo "   Source: $SKILLS_SOURCE"
echo "   Context: $BUILD_CONTEXT"
echo ""

# Check if skills exist
if [ ! -d "$SKILLS_SOURCE" ]; then
    echo "❌ Error: Skills not found at $SKILLS_SOURCE"
    echo "   Run: cd ~/alteriom-claude-skills && ./scripts/convert-to-crewai.sh"
    exit 1
fi

# Copy skills into build context (temporary)
echo "📦 Copying skills to build context..."
rm -rf "$BUILD_CONTEXT/skills"
cp -r "$SKILLS_SOURCE" "$BUILD_CONTEXT/skills"

# Count skills
SKILL_COUNT=$(find "$BUILD_CONTEXT/skills" -name "SKILL.md" | wc -l)
echo "   ✅ Copied $SKILL_COUNT skills"
echo ""

# Build Docker image
echo "🐳 Building Docker image..."
cd "$BUILD_CONTEXT"

# Get version from git tag or use dev
VERSION="${1:-dev}"
if [ "$VERSION" = "dev" ]; then
    # Use short commit hash for dev builds
    if git rev-parse --git-dir > /dev/null 2>&1; then
        GIT_HASH=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
        VERSION="dev-$GIT_HASH"
    fi
fi

IMAGE_NAME="ghcr.io/alteriom/crew-runner"
IMAGE_TAG="$VERSION"

docker build \
    --tag "$IMAGE_NAME:$IMAGE_TAG" \
    --tag "$IMAGE_NAME:latest" \
    --build-arg VERSION="$VERSION" \
    --file Dockerfile \
    .

echo ""
echo "✅ Build complete!"
echo "   Image: $IMAGE_NAME:$IMAGE_TAG"
echo "   Skills: $SKILL_COUNT"
echo ""
echo "🚀 Next steps:"
echo "   - Test locally: docker run -p 8081:8081 $IMAGE_NAME:$IMAGE_TAG"
echo "   - Push to GHCR: docker push $IMAGE_NAME:$IMAGE_TAG"
echo "   - Deploy to production"
echo ""

# Clean up build context
echo "🧹 Cleaning up..."
rm -rf "$BUILD_CONTEXT/skills"
echo "   ✅ Removed temporary skills directory"
