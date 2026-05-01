import json
from collections import Counter

stats = Counter()

with open("logs/bot.log") as f:
    for line in f:
        try:
            log = json.loads(line)
            event = log.get("event")

            if event:
                stats[event] += 1

        except:
            continue

print(dict(stats))
