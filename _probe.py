import time, sys, os
sys.path.insert(0, r"E:\Projects\lead_scraper")
t0 = time.time()
import db
print("imported db", round(time.time() - t0, 1), flush=True)
db.init_db()
print("db.init_db done", round(time.time() - t0, 1), flush=True)