# Commandes simplifiées pour gérer le bot

.PHONY: start stop restart logs build status paper live

start:
	docker-compose up -d

stop:
	docker-compose down

restart:
	docker-compose restart

build:
	docker-compose build --no-cache

logs:
	docker-compose logs -f --tail=100

status:
	docker-compose ps

# Lancement local sans Docker (dev)
run:
	python bot.py

# Bascule paper → live (modifie .env)
paper:
	sed -i 's/BOT_MODE=live/BOT_MODE=paper/' .env && \
	sed -i 's/HYPERLIQUID_TESTNET=false/HYPERLIQUID_TESTNET=true/' .env && \
	docker-compose restart

live:
	@echo "⚠️  Activation du mode LIVE — capital réel"
	@read -p "Confirmer ? (CONFIRMER) : " c; [ "$$c" = "CONFIRMER" ] && \
	sed -i 's/BOT_MODE=paper/BOT_MODE=live/' .env && \
	sed -i 's/HYPERLIQUID_TESTNET=true/HYPERLIQUID_TESTNET=false/' .env && \
	docker-compose restart || echo "Annulé"