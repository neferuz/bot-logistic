import sqlite3
import difflib

def SIMILARITY(q, t):
    return difflib.SequenceMatcher(None, q.lower() if q else "", t.lower() if t else "").ratio()

db = sqlite3.connect('data/logistic_bot.db')
db.create_function("LOWER", 1, lambda x: x.lower() if x else x)
db.create_function("SIMILARITY", 2, SIMILARITY)

cargo_from = "Ташкент"
cargo_to = "Баку"

query = """SELECT 
            COALESCE(route, direction, ''), 
            sender_id, 
            chat_link, 
            COALESCE(text, message_text, ''), 
            timestamp, 
            COALESCE(msg_id, message_id, 0), 
            id 
           FROM cargo_cache"""
params = []
conditions = []

search_pattern = """(
    LOWER(route) LIKE LOWER(?) OR LOWER(direction) LIKE LOWER(?) OR LOWER(text) LIKE LOWER(?) OR LOWER(message_text) LIKE LOWER(?) 
    OR SIMILARITY(?, route) > 0.8 OR SIMILARITY(?, direction) > 0.8
)"""

def is_wildcard(s):
    if not s: return True
    return s.strip().lower() in ["любой", "все", "везде", ".", "-", "any", "all", "*"]

if not is_wildcard(cargo_from):
    conditions.append(search_pattern)
    p = f"%{cargo_from}%"
    params.extend([p, p, p, p, cargo_from, cargo_from])
if not is_wildcard(cargo_to):
    conditions.append(search_pattern)
    p = f"%{cargo_to}%"
    params.extend([p, p, p, p, cargo_to, cargo_to])

if conditions:
    query += " WHERE " + " AND ".join(conditions)
    query += " AND sender_id NOT IN (SELECT sender_id FROM cargo_cache WHERE timestamp > datetime('now', '-1 day') GROUP BY sender_id HAVING COUNT(DISTINCT group_id) > 30)"

query += " GROUP BY COALESCE(text, message_text, '')"
query += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
params.extend([10, 0])

cursor = db.cursor()
cursor.execute(query, params)
results = cursor.fetchall()
print(f"Found {len(results)} results")
for r in results:
    print(f"Route: {r[0]}")
    # print(f"Text: {r[3][:100]}...")
