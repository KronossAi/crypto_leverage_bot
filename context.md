# CRYPTO LEVERAGE BOT — CONTEXTE COMPLET
**Dernière mise à jour : 30/04/2026 — post-rebuild VPS Hetzner + reprise sur MSI Cyborg 15**

---

## 🏗️ INFRASTRUCTURE

### Hardware & OS
- **VPS** : Hetzner Cloud CAX11 (ARM aarch64, 2 vCPU, 4GB RAM, 40GB disque, ~5,39€/mois)
- **OS** : Ubuntu 24.04 LTS (réinstallé proprement le 30/04/2026 après crash LUKS)
- **IP** : 46.225.52.233
- **Hostname** : Cryptobot-pc
- **Python** : 3.12 venv dans `/root/crypto_leverage_bot/venv`
- **Screen permanent** : `cryptobot` (lancé via `screen -S cryptobot`)

### PC de développement
- **MSI Cyborg 15 A13V** (i7-13620H + RTX 4060 + 16GB DDR5)
- **OS** : Windows 11
- **Repo local** : `C:\Users\erwan\Dev\crypto_leverage_bot\`
- **IDE** : VS Code (avec extensions Python, GitLens, Remote-SSH)
- **Python** : 3.12.10 installé localement
- **Terminal** : PowerShell

### Sources de données & exécution
| Service | Rôle | Status |
|---------|------|--------|
| **Bybit V5 WebSocket** | Données marché (publicTrade, kline, orderbook, forceOrder) | ✅ Actif (mainnet) |
| **Hyperliquid SDK** | Exécution ordres | ✅ Actif (testnet pour paper) |
| **Telegram Bot API** | Notifications + commandes | ✅ Actif |
| **GitHub** | Versionning code | ✅ Actif |

### Authentification
- **SSH** : clé ed25519 `msi-cyborg-15` installée sur VPS, password désactivé
- **GitHub** : Personal Access Token actif (Bitwarden)
- **Hetzner** : compte Cloud avec backups + snapshots

### Pourquoi Bybit pour les données
Binance Futures bloque silencieusement les streams `aggTrade` et `kline` depuis l'IP du VPS Hetzner. Migration vers Bybit V5 le 28/04/2026 = 100% des streams opérationnels.

---

## ⚙️ CONFIGURATION ACTIVE

### Capital & Risk
- Capital initial : 200 USDC (perdu après rebuild — restart à 200 USDC le 30/04)
- Apport mensuel prévu : 100 USDC
- Risk per trade : 1% du capital
- Daily loss limit : -5% (circuit breaker)
- Max drawdown : -12% (circuit breaker hard stop)

### Trading
- Mode : Paper (HYPERLIQUID_TESTNET=true)
- Paires : BTC, ETH, SOL, XRP, XLM
- Timeframes : HTF=4h / MTF=1h / LTF=5m / Trigger=1m
- Levier défaut : x5 (max x10)
- Max positions simultanées : 3
- Max trades/jour : 12

### Funding rate filtres
- `long_block_threshold` : 0.001 (>0.1% → bloque longs)
- `short_block_threshold` : -0.0005 (<-0.05% → bloque shorts)
- `extreme_negative` : -0.002 (squeeze probable → override SHORT vers LONG)

---

## 🧠 ARCHITECTURE L2-DRIVEN

### Principe (post-28/04/2026)
**L2 vote sa propre direction**, L1 sert d'informant + booster, L3 confirme le timing.

### Pipeline `orchestrator.analyze()`
1. Circuit breaker check
2. Macro filter check (calendrier news)
3. Régime detector (HV/LV/normal via ATR)
4. **Layer 1** — HTF informatif (htf_hint), funding extreme = block
5. **Layer 2** — Vote autonome (SMC + AVWAP + RSI), min 2/3 alignés
6. Validation L1↔L2 — si HTF strict opposé = BLOCK
7. **Layer 3** — LTF trigger (EMA9/21 + CVD + volume spike, min 2/3)
8. SL/TP calculation (ATR-based + OB si disponible)
9. R/R check (minimum 2.0)
10. Confidence calculation :
    - Base 0.5 + L2 score × 0.1 + L3 trigger 0.15 + HV regime 0.05
    - +10% si L1↔L2 aligné, -5% si counter-trend
    - OrderFlow boost ±10% (Module 1)
    - Tape boost ±5% (Module 2)
    - OBI boost ±8% ou BLOCK si VPIN toxique (Module 3)
    - VWAP boost ±7% (Module 4)
    - KeyLevels boost (Module 5)
    - OI boost (Module 6)
    - Liquidation boost ±5% (Module 7)
11. SIGNAL émis si confidence finale ≥ seuil

---

## 📁 STRUCTURE FICHIERS
```
~/crypto_leverage_bot/
├── bot.py                          # Entry point, orchestre toutes les tasks
├── RECOVERY.md                     # Procédures de récupération (NEW 30/04)
├── config/
│   ├── config.yaml                 # Capital, paires, timeframes, limites
│   └── strategies.yaml             # Paramètres stratégies (SMC, momentum, MR, AVWAP)
├── core/
│   ├── exchange.py                 # Client Hyperliquid SDK
│   ├── fsm.py                      # IDLE→SIGNAL→OPEN→TP1_HIT→MANAGE→CLOSE
│   ├── risk_manager.py             # Sizing, calcul order, frais
│   ├── portfolio.py                # Capital, PnL net, métriques
│   ├── circuit_breaker.py          # Daily loss, consec losses, ATR spike
│   └── state_manager.py            # Persistance état JSON (data/state.json)
├── data/
│   ├── feed.py                     # ⭐ Bybit V5 WebSocket (publicTrade/kline/orderbook)
│   ├── indicators.py               # EMA, RSI, BB, ATR, CVD, AVWAP, SMC
│   ├── macro_filter.py             # Calendrier macro statique
│   ├── scalp_orderflow.py          # Module 1+2: Footprint + Tape Reading
│   ├── scalp_levels.py             # Module 3: OBI L2, Walls, VPIN
│   ├── scalp_timing.py             # Module 4: VWAP Sessions
│   ├── scalp_levels_key.py         # Module 5: PDH/PDL/IB/Round Numbers
│   ├── scalp_oi.py                 # Module 6: OI + L/S Ratio
│   ├── scalp_liquidations.py       # Module 7: Liquidation Heatmap
│   └── state.json                  # État persisté (capital, positions, trades)
├── engine/
│   ├── paper_engine.py             # Simulation TP1/TP2/SL/Trailing/Funding
│   ├── live_engine.py              # Exécution réelle Hyperliquid
│   └── switcher.py                 # Bascule paper↔live avec checklist
├── strategies/
│   ├── orchestrator.py             # ⭐ Pipeline L1→L2→L3 + boosts modules 1-7
│   ├── layer1.py                   # HTF bias informatif (EMA50/200 BTC + funding)
│   ├── layer2.py                   # ⭐ MTF VOTE autonome (SMC+AVWAP+RSI)
│   ├── layer3.py                   # LTF trigger (EMA9/21 + CVD + volume spike)
│   └── regime_detector.py          # HV/LV/normal via ATR14 vs ATR50
├── notifications/
│   └── telegram_bot.py             # 7 commandes + alertes (open/close/TP1)
├── dashboard/
│   └── cli_dashboard.py            # Dashboard Rich terminal temps réel
├── logs/
│   └── bot.log                     # Logs JSON structurés
└── venv/                           # Python virtual environment
```

---

## 🧩 MODULES IMPLÉMENTÉS (1-7)

### Module 1 — Footprint & Delta
**Fichier** : `data/scalp_orderflow.py` → `FootprintEngine`
**Source** : Bybit `publicTrade` WebSocket
**Détecte** : CVD divergences, Delta Exhaustion, Stacked Imbalance, Absorption, Naked POC, HVN Rejection
**Impact confidence** : ±10%

### Module 2 — Tape Reading
**Fichier** : `data/scalp_orderflow.py` → `TapeReader`
**Détecte** : Large Print, Aggressive Sequence, Iceberg, Speed of Tape, Trade Size Distribution
**Impact confidence** : ±5%

### Module 3 — Order Book Imbalance L2
**Fichier** : `data/scalp_levels.py` → `OrderBookAnalyzer` + `OBIRegistry`
**Source** : Bybit `orderbook.50` WebSocket
**Détecte** : OBI Ratio, Depth Ratio, Walls, Spoofing, VPIN
**Impact confidence** : ±8% ou BLOCK total si VPIN toxique

### Module 4 — VWAP Sessions
**Fichier** : `data/scalp_timing.py` → `VWAPEngine` + `VWAPRegistry`
**Sessions UTC** : Asian (00h-08h) / London (07h-10h) / NY (13h-16h)
**Détecte** : Touch ±1σ/±2σ/±3σ, reversion, extension extrême
**Impact confidence** : ±7%

### Module 5 — Key Levels
**Fichier** : `data/scalp_levels_key.py` → `KeyLevelsRegistry`
**Détecte** : PDH/PDL, PWH/PWL, Round Numbers (00/50), Initial Balance, Overnight Range, Previous Session Close, Monthly Open
**Impact confidence** : boost variable

### Module 6 — Open Interest + L/S Ratio
**Fichier** : `data/scalp_oi.py` → `OIRegistry`
**Source** : Bybit OI + Long/Short Ratio
**Détecte** : OI spike+price, OI divergence, L/S ratio extrême, Taker B/S, OI Squeeze
**Impact confidence** : boost variable

### Module 7 — Liquidation Heatmap
**Fichier** : `data/scalp_liquidations.py` → `LiquidationRegistry`
**Source** : Bybit `forceOrder` WebSocket (gratuit)
**Détecte** : Clusters au-dessus/en-dessous prix → magnets
**Impact confidence** : ±5%

---

## 🐛 BUGS CRITIQUES FIXÉS (28/04/2026)

| # | Fichier | Bug | Status |
|---|---------|-----|--------|
| 1 | `layer1.py` | return manquant | ✅ |
| 2 | `feed.py` | CVD/depth callbacks non wirés | ✅ |
| 3 | `fsm.py + paper_engine.py + state_manager.py` | Double PnL TP1 | ✅ |
| 4 | `orchestrator.py` | OB sl_dist négatif | ✅ |
| 5 | `macro_filter.py` | delta.total_seconds() | ✅ |
| 6 | `circuit_breaker.py` | remaining crash potentiel | ✅ |
| 7 | `indicators.py` | RSI divergence seuil | ✅ |
| 8 | `orchestrator.py` | confidence asymétrique | ✅ |
| 9 | `dashboard/cli_dashboard.py` | pnl_usdc → pnl_net | ✅ |
| 10 | `portfolio.py` | `_daily_trades` reset au changement de jour | ✅ |
| 11 | `indicators.py` | `_find_swing` retournait extrême au lieu du dernier (AVWAP) | ✅ |
| 12 | `indicators.py` | `calc_smc` ajout détection HH/HL | ✅ |
| 13 | `indicators.py` | `calc_ema_trend` permissif + strict en backup | ✅ |
| 14 | `indicators.py` | `CVDTracker.get()` retour vide manquait `bearish` | ✅ |
| 15 | `layer2.py` | Réécriture L2-driven (vote propre) | ✅ |
| 16 | `orchestrator.py` | Utilisation `htf_hint` + validation strict | ✅ |
| 17 | `feed.py` | Migration complète Binance → Bybit V5 | ✅ |
| 18 | `paper_engine.py + bot.py` | Notifications Telegram ouverture/fermeture | ✅ |

---

## 📜 INCIDENT MAJEUR — 30/04/2026 : CRASH LUKS + REBUILD

### Contexte
Tentative d'installation Ubuntu 24.04 chiffrée (LUKS) via `installimage` Hetzner pour "améliorer la sécurité". `dropbear-initramfs` non configuré → serveur bloqué au boot, impossible de saisir la passphrase à distance.

### Symptôme
- SSH `Permission denied` puis `Connection timed out`
- Bot Telegram ne répond plus
- Reboot et rescue mode infructueux car mauvaise partition montée

### Récupération
1. ✅ Désactivation rescue mode
2. ✅ Rebuild propre via Hetzner Cloud Console (Ubuntu 24.04 standard, sans LUKS)
3. ✅ Récupération mot de passe root par email Hetzner
4. ✅ Réinstallation SSH key ed25519 du MSI Cyborg
5. ✅ Updates système (apt upgrade)
6. ✅ Installation outils (Python 3.12, git, screen, ufw, fail2ban)
7. ✅ Sécurisation (UFW, fail2ban, password auth SSH désactivé)
8. ✅ Clone repo GitHub
9. ✅ venv + requirements.txt
10. ✅ Reconfig `.env` avec clés sauvegardées
11. ✅ Bot relancé dans screen
12. ✅ Backups Hetzner activés
13. ✅ Création `RECOVERY.md` à la racine du repo

### Données perdues
- Historique trades paper (12 trades, capital 198.79 USDC)
- Logs historiques avant 30/04
- State.json précédent

### Données conservées
- ✅ Code complet (GitHub intact)
- ✅ Configuration (.env recréé depuis Bitwarden)
- ✅ Architecture L2-driven (toutes les modifs des sessions précédentes)
- ✅ Modules 1-7 fonctionnels

### Leçons retenues
1. **LUKS sur VPS Hetzner = ROI faible** (Hetzner peut accéder physiquement, donc protection théorique)
2. **Sécurité réelle** = SSH key, fail2ban, UFW, backups, secrets dans Bitwarden — PAS LUKS
3. **Toujours faire un snapshot AVANT toute manipulation risquée** (gratuit et instantané sur Hetzner)
4. **Backups Hetzner activés** = filet de sécurité ultime (~1€/mois)

---

## 🔐 SÉCURITÉ ACTUELLE (post-30/04)

| Mesure | Status |
|--------|--------|
| SSH key only (password auth désactivé) | ✅ |
| UFW firewall (port 22 uniquement) | ✅ |
| fail2ban | ✅ |
| Secrets hors GitHub (`.env` dans `.gitignore`) | ✅ |
| Bitwarden pour clés/tokens/passwords | ✅ |
| Backups Hetzner automatiques quotidiens | ✅ |
| Snapshot manuel post-recovery | ✅ |
| Updates système réguliers (apt upgrade) | À automatiser |

---

## 🚧 CHANTIERS EN COURS / À VENIR

### Priorité haute (immédiate)
- [ ] **Validation 24-48h paper trading** (post-rebuild) avec stats stables
- [ ] **Bug `calc_volume_spike`** : utilise bougie en formation (`iloc[-1]`) → ratio 0.0X aberrant

### Priorité haute (semaine)
- [ ] **Risk Engine refonte** : Kelly fractionnel 20%, TP1/TP2/TP3, sizing ATR par régime
- [ ] **Scoring gate unifié** : 65/100 scalping, 70/100 swing
- [ ] **Cleanup logs DEBUG** une fois pipeline stabilisé

### Priorité moyenne (Tier 2 - 12-15h dev)
- [ ] Module 8 — Micro-patterns 1M/3M/5M
- [ ] Module 9 — Candlestick TA-Lib
- [ ] Module 10 — Volume Profile VPVR
- [ ] Module 11 — ICT Avancé (Breaker, IFVG, BSL/SSL, OTE, Killzones)
- [ ] Module 12 — Dual-Regime Detector (ADX + CI + BBW + ATR% + Hurst)
- [ ] Module 13 — Indicateurs complémentaires (SuperTrend, StochRSI, MFI, BBSqueeze)
- [ ] Module 14 — Crypto-specific (Funding Flush, OI Squeeze, Perp/Spot Basis)

### Priorité basse (Tier 3 - 10-12h dev)
- [ ] Module 15 — Chart Patterns géométriques
- [ ] Module 16 — Patterns Harmoniques
- [ ] Module 17 — Market Profile TPO
- [ ] Module 18 — Derivatives Data (Put/Call, Max Pain, GEX)
- [ ] Module 19 — Macro/Événementiel (Finnhub, FMP)

### Pré-live trading
- [ ] Backtesting framework (vectorbt ou backtrader)
- [ ] Walk-forward analysis sur 6+ mois historiques
- [ ] Monte Carlo stress tests
- [ ] Validation 30+ jours paper avec stats stables
- [ ] Checklist sécurité Hyperliquid mainnet

### Améliorations diverses
- [ ] Suppression doublon `data/telegram_bot.py` (orphelin)
- [ ] Dédoublement signaux orchestrator (déjà OPEN → skip silencieux)
- [ ] Dashboard web (Grafana ou custom)
- [ ] Migration vers SQLite pour state persistence (au lieu de JSON)
- [ ] Tests unitaires sur risk_manager, fsm, portfolio

---

## 🛠️ COMMANDES UTILES

### Workflow Git PC ↔ GitHub ↔ VPS

**Sur PC (MSI Cyborg) — PowerShell** :
```powershell
cd $env:USERPROFILE\Dev\projects\crypto_leverage_bot
git fetch origin main
git reset --hard origin/main
# modifs dans VS Code
git add . && git commit -m "..." && git push origin main
```

**Sur VPS** :
```bash
cd ~/crypto_leverage_bot
git pull origin main
```

**Push direct VPS depuis PC** :
```powershell
ssh root@46.225.52.233 "cd ~/crypto_leverage_bot && git pull origin main"
```

### Redémarrage propre du bot
```bash
ssh root@46.225.52.233
screen -S cryptobot -X quit
sleep 2
cd ~/crypto_leverage_bot && source venv/bin/activate
screen -S cryptobot
python bot.py
# Ctrl+A puis D pour détacher
```

### Surveillance logs
```bash
# Tail filtré
tail -f ~/crypto_leverage_bot/logs/bot.log | grep -E "SIGNAL|TP1|TP2|SL|PAPER|FSM"

# Stats rapides
grep -c "SIGNAL" ~/crypto_leverage_bot/logs/bot.log
grep "Trade ferme" ~/crypto_leverage_bot/logs/bot.log | tail -5
grep "Capital:" ~/crypto_leverage_bot/logs/bot.log | tail -1

# Erreurs
grep -iE "ERROR|Exception" ~/crypto_leverage_bot/logs/bot.log | tail -10
```

### Bybit data check
```bash
# Vérifier WS reçoit bien tous les types
grep "FEED DEBUG" ~/crypto_leverage_bot/logs/bot.log | tail -3
```

### Vérification serveur
```bash
# Bot tourne ?
screen -ls

# Sessions SSH actives
who

# Disque
df -h

# RAM
free -h

# Charge
uptime
```

---

## 📌 NOTES IMPORTANTES

- **Bybit V5 ne nécessite AUCUNE clé API** pour les flux publics (data feed)
- **Hyperliquid garde le rôle d'exécution** (testnet en paper, mainnet en live)
- Variables `BYBIT_API_KEY/SECRET` dans `.env.example` = futures fonctionnalités (arbitrage, données privées)
- **Reset capital via commande Telegram** disponible (`/reset_capital`)
- **GitHub Token** géré par Git Credential Manager sur PC, embedded dans remote URL sur VPS
- **State persisté** dans `data/state.json` (capital, positions, trades) → restauré au redémarrage
- **`.env` JAMAIS dans Git** (vérifier `.gitignore`)
- **Backups Hetzner activés** (~1€/mois, sauvegarde quotidienne automatique)
- **Snapshot manuel** créé post-recovery 30/04 (état "clean")

---

## 📂 Bitwarden — Coffre-fort à maintenir

À garder absolument :
- ✅ Mot de passe root VPS (initial Hetzner + post-changement éventuel)
- ✅ Clé privée SSH `id_ed25519` MSI Cyborg
- ✅ Token Telegram bot (`TELEGRAM_BOT_TOKEN`)
- ✅ Telegram chat ID
- ✅ Clé privée Hyperliquid testnet
- ✅ Mot de passe compte Hetzner Cloud
- ✅ Token GitHub PAT (créé 30/04, expire dans 90 jours)

---

## 🔗 Liens utiles

- **GitHub Repo** : https://github.com/KronossAi/crypto_leverage_bot
- **Hetzner Cloud Console** : https://console.hetzner.cloud/
- **Bybit V5 Docs** : https://bybit-exchange.github.io/docs/v5/intro
- **Hyperliquid Docs** : https://hyperliquid.gitbook.io/hyperliquid-docs/
- **Telegram BotFather** : @BotFather
- **Telegram User ID Bot** : @userinfobot

---

## 🗓️ Dernière session de développement

**30/04/2026 — Récupération crash + setup nouveau PC**
- Setup MSI Cyborg 15 A13V (Python 3.12, VS Code, Git, SSH)
- Crash LUKS sur VPS Hetzner suite tentative installimage
- Rebuild Ubuntu 24.04 propre + sécurisation
- Bot opérationnel à nouveau
- Documentation `RECOVERY.md` créée
- Token GitHub renouvelé (sécurité)

**Prochaine session prévue** :
- Validation 24-48h paper trading
- Décision : Risk Engine refonte OU modules Tier 2 d'abord