#!/bin/bash

OUT="/root/crypto_leverage_bot/monitor/status.log"
DATE=$(date '+%F %T')

echo "===== $DATE =====" >> $OUT
docker ps --filter name=crypto_leverage_bot >> $OUT

echo "--- CPU/RAM ---" >> $OUT
docker stats --no-stream crypto_leverage_bot >> $OUT

echo "--- BOT LOG FILE ---" >> $OUT
tail -30 /root/crypto_leverage_bot/logs/bot.log >> $OUT 2>&1

echo "" >> $OUT
