#!/bin/bash

LOG_FILE="logs/bot.log"
OUT_FILE="monitor_24h.log"

echo "=== Monitoring démarré $(date) ===" | tee -a $OUT_FILE

# Boucle 24h (144 cycles de 10min)
for i in $(seq 1 144); do
    echo "=== Check $(date) | Cycle $i/144 ===" | tee -a $OUT_FILE

    echo "--- Dernières 20 lignes log ---" | tee -a $OUT_FILE
    tail -n 20 $LOG_FILE | tee -a $OUT_FILE

    echo "--- ERROR count ---" | tee -a $OUT_FILE
    grep "ERROR" $LOG_FILE | wc -l | tee -a $OUT_FILE

    echo "--- SIGNAL count ---" | tee -a $OUT_FILE
    grep "SIGNAL" $LOG_FILE | wc -l | tee -a $OUT_FILE

    echo "--- REJECT LOW CONFIDENCE count ---" | tee -a $OUT_FILE
    grep "REJECT LOW CONFIDENCE" $LOG_FILE | wc -l | tee -a $OUT_FILE

    echo "Cycle terminé, pause 10min..." | tee -a $OUT_FILE
    sleep 600  # 600s = 10min
done

echo "=== Monitoring terminé $(date) ===" | tee -a $OUT_FILE
