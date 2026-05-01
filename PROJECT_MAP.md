# PROJECT_MAP.md

CRYPTO_LEVERAGE_BOT/
├── .vscode/settings.json
├── config/
│   ├── config.yaml
│   └── strategies.yaml
├── context/
│   ├── chat_01.xml
│   ├── chat_02.xml
│   ├── chat_03.xml
│   ├── chat_04.xml
│   ├── chat_05.xml
│   ├── chat_06.xml
│   ├── chat_07.xml
│   ├── chat_08.xml
│   ├── chat_09.xml
│   ├── chat_10.xml
│   ├── chat_11.xml
│   └── chat_12.xml
├── core/
│   ├── circuit_breaker.py
│   ├── exchange.py
│   ├── fsm.py
│   ├── portfolio.py
│   ├── risk_manager.py
│   └── state_manager.py
├── dashboard/
│   └── cli_dashboard.py
├── data/
│   ├── feed.py
│   ├── indicators.py
│   ├── macro_filter.py
│   ├── scalp_levels_key.py
│   ├── scalp_levels.py
│   ├── scalp_liquidations.py
│   ├── scalp_oi.py
│   ├── scalp_orderflow.py
│   ├── scalp_timing.py
│   └── telegram_bot.py
├── engine/
│   ├── live_engine.py
│   ├── paper_engine.py
│   └── switcher.py
├── handoffs/
├── notifications/
│   └── telegram_bot.py
├── strategies/
│   ├── __init__.py
│   ├── layer1.py
│   ├── layer2.py
│   ├── layer3.py
│   ├── orchestrator.py
│   └── regime_detector.py
├── tools/
│   └── open_chat.py
├── .env.example
├── .gitignore
├── bot.py
├── create_contexts.py
├── deploy.sh
├── docker-compose.yml
├── Dockerfile
├── feed.py
├── Makefile
├── PROJECT_MAP.md
├── RECOVERY.md
├── requirements.txt
└── test_telegram.py
