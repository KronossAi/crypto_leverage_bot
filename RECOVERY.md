# RECOVERY — Procédures de récupération du bot

> Ce document recense toutes les procédures de récupération en cas d'incident.
> À consulter AVANT toute manipulation risquée sur le serveur.

---

## Cas 1 — Le bot a crashé mais le serveur SSH est OK

```bash
ssh cryptobot
screen -r cryptobot
# Si screen n'existe plus :
cd ~/crypto_leverage_bot
source venv/bin/activate
screen -S cryptobot
python bot.py
# Ctrl+A puis D pour détacher
```

---

## Cas 2 — Plus d'accès SSH (clé cassée)

1. Hetzner Cloud Console → ouvrir la **Console** web (icône `>_`)
2. Login avec mot de passe root (gardé dans Bitwarden)
3. Réparer SSH :
```bash
   nano ~/.ssh/authorized_keys
   # Ajouter clé publique du nouveau PC
   chmod 600 ~/.ssh/authorized_keys
```

---

## Cas 3 — Mot de passe root perdu ET clés SSH KO

1. Hetzner Cloud Console → Activer **Rescue mode** (Ubuntu 22.04 64bit)
2. Reboot serveur en rescue
3. SSH en rescue : `ssh root@46.225.52.233` (mot de passe rescue affiché par Hetzner)
4. Monter la partition racine :
```bash
   lsblk                       # identifier la bonne partition
   mount /dev/sdaX /mnt        # X = numéro racine, généralement la plus grande
   chroot /mnt
   passwd root                 # nouveau mot de passe
   exit
   umount /mnt
```
5. Désactiver rescue dans Hetzner
6. Reboot

---

## Cas 4 — Serveur complètement HS / OS corrompu

**Restore depuis backup Hetzner** :
1. Cloud Console → Cryptobot-pc → onglet **Backups**
2. Choisir le backup le plus récent
3. Cliquer **Restore**
4. Confirmer
5. Attendre 5-10 min
6. Bot redémarre automatiquement (screen `cryptobot` à recréer manuellement après reboot)

---

## Cas 5 — Migration nouveau VPS

```bash
# Sur le nouveau VPS, après installation Ubuntu 24.04 :
apt update && apt upgrade -y
apt install -y git python3.12 python3.12-venv python3-pip screen htop nano ufw fail2ban

# Setup SSH key (depuis le PC)
ssh-copy-id root@NOUVELLE_IP

# Clone et setup
git clone https://github.com/KronossAi/crypto_leverage_bot.git
cd crypto_leverage_bot
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# .env (depuis Bitwarden)
cp .env.example .env
nano .env  # remplir les vraies valeurs

# Lancer
screen -S cryptobot
python bot.py
# Ctrl+A puis D
```

---

## ⚠️ Choses à NE JAMAIS faire

- ❌ Modifier `/etc/ssh/sshd_config` sans tester sur une seconde session SSH ouverte
- ❌ Désactiver le password auth SSH avant de vérifier que la clé fonctionne
- ❌ Faire `installimage` avec config LUKS sans dropbear-initramfs configuré
- ❌ Faire `rm -rf` dans `/mnt` sans avoir vérifié que c'est la bonne partition (`lsblk`)
- ❌ Toucher au firewall UFW sans avoir un fallback (Hetzner Console)
- ❌ Stocker `.env` dans Git (vérifier `.gitignore`)

---

## 🔐 Coffre-fort (Bitwarden ou autre)

À garder absolument dans le coffre :
- Mot de passe root VPS (initial Hetzner + post-changement)
- Clé privée SSH (`id_ed25519`)
- Token Telegram bot (`TELEGRAM_BOT_TOKEN`)
- Telegram chat ID
- Clé privée Hyperliquid testnet
- Mot de passe Hetzner Cloud Console
- Token GitHub (PAT)

---

## 📋 Commandes de monitoring rapide

```bash
# Bot tourne ?
screen -ls

# Logs récents
tail -50 ~/crypto_leverage_bot/logs/bot.log

# Erreurs récentes
grep -iE "ERROR|Exception" ~/crypto_leverage_bot/logs/bot.log | tail -10

# Compteur signaux du jour
grep -c "SIGNAL" ~/crypto_leverage_bot/logs/bot.log

# Dernière entrée portfolio
grep "Capital:" ~/crypto_leverage_bot/logs/bot.log | tail -1

# Trades fermés récents
grep "Trade ferme" ~/crypto_leverage_bot/logs/bot.log | tail -5
```

---

## 🆘 Numéros utiles

- **Hetzner Cloud Console** : https://console.hetzner.cloud/
- **GitHub Repo** : https://github.com/KronossAi/crypto_leverage_bot
- **Telegram BotFather** : @BotFather (pour récupérer/régénérer un token)
- **Telegram User ID Bot** : @userinfobot (pour récupérer son chat_id)

---

## 🗓️ Historique des incidents

### 2026-04-30 — Incident installimage LUKS

**Symptôme** : Serveur reboot bloqué au boot après installation Ubuntu 24.04 chiffrée (LUKS) sans `dropbear-initramfs` configuré → impossible d'entrer la passphrase à distance.

**Récupération** : Rebuild propre via Cloud Console (Ubuntu 24.04 standard, sans chiffrement) + réinstallation complète du bot. Durée : ~1h.

**Leçon** : Pour un VPS Hetzner, le chiffrement LUKS n'apporte pas de protection significative et augmente énormément la complexité. Préférer setup standard + backups Hetzner activés + snapshots avant manipulation risquée.