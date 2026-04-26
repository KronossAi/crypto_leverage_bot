#!/bin/bash
# ─────────────────────────────────────────────────────────────
# Script de déploiement VPS — Ubuntu 22.04
# Usage : chmod +x deploy.sh && ./deploy.sh
# ─────────────────────────────────────────────────────────────
set -e

PROJECT_DIR="$HOME/crypto_leverage_bot"
REPO_URL=""   # Ajoute ton repo git ici si applicable

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  🚀 Déploiement Crypto Leverage Bot"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── 1. Mise à jour système ────────────────────────────────────
echo "📦 Mise à jour système..."
sudo apt-get update -qq
sudo apt-get install -y -qq \
    docker.io docker-compose \
    git curl ufw fail2ban

# ── 2. Sécurité firewall ──────────────────────────────────────
echo "🔒 Configuration firewall..."
sudo ufw --force reset
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow ssh
sudo ufw --force enable

# ── 3. Fail2ban (protection SSH brute-force) ──────────────────
sudo systemctl enable fail2ban
sudo systemctl start fail2ban

# ── 4. Docker sans sudo ───────────────────────────────────────
sudo usermod -aG docker $USER

# ── 5. Dossier projet ─────────────────────────────────────────
echo "📁 Préparation du projet..."
mkdir -p "$PROJECT_DIR"
cd "$PROJECT_DIR"
mkdir -p logs config

# ── 6. Fichier .env ───────────────────────────────────────────
if [ ! -f .env ]; then
    echo "⚙️  Création du fichier .env..."
    cat > .env << 'EOF'
PRIVATE_KEY=0xyour_private_key_here
WALLET_ADDRESS=0xyour_wallet_address_here
HYPERLIQUID_TESTNET=true
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
BOT_MODE=paper
LOG_LEVEL=INFO
EOF
    echo "⚠️  Remplis le fichier .env avant de continuer !"
    echo "   nano $PROJECT_DIR/.env"
    exit 1
fi

# ── 7. Build et lancement ─────────────────────────────────────
echo "🐳 Build Docker..."
docker-compose build --no-cache

echo "🟢 Lancement du bot..."
docker-compose up -d

# ── 8. Vérification ───────────────────────────────────────────
sleep 3
docker-compose ps
docker-compose logs --tail=20

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ✅ Déploiement terminé"
echo ""
echo "  Commandes utiles :"
echo "  docker-compose logs -f          # Logs live"
echo "  docker-compose restart          # Redémarrer"
echo "  docker-compose down             # Arrêter"
echo "  docker-compose exec trading-bot python -c 'print(\"OK\")'"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"