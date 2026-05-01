import sys, pyperclip
from pathlib import Path

CHATS = {1:'chat_01_daily_ops.xml',2:'chat_02_risk_engine.xml',3:'chat_03_modules_tier2.xml',4:'chat_04_modules_tier3.xml',5:'chat_05_scoring_gate.xml',6:'chat_06_architecture.xml',7:'chat_07_backtesting.xml',8:'chat_08_live_prep.xml',9:'chat_09_bug_debug.xml',10:'chat_10_perf_opti.xml',11:'chat_11_strat_theorie.xml',12:'chat_12_analyse_marche.xml'}
BASE = Path(__file__).parent.parent
CONTEXT_DIR = BASE / 'context'
HANDOFF_DIR = BASE / 'handoffs'

def build_message(chat_id):
    xml = (CONTEXT_DIR / CHATS[chat_id]).read_text(encoding='utf-8')
    handoff_file = HANDOFF_DIR / f'handoff_to_{chat_id:02d}.md'
    handoff_block = f'\n\n<handoff>\n{handoff_file.read_text(encoding="utf-8")}\n</handoff>' if handoff_file.exists() else ''
    return f'INIT CHAT {chat_id}\n<context>\n{xml}\n</context>{handoff_block}\n\nAccuse réception en 1 ligne.'

chat_id = int(sys.argv[1])
stdout = '--stdout' in sys.argv
msg = build_message(chat_id)
if stdout:
    print(msg)
else:
    pyperclip.copy(msg)
    print(f'OK Chat {chat_id} copie ({len(msg)} chars)')
