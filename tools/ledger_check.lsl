# Independent market invariant check written in LSL on top of sqlite 1.0.0.
#
# It reads the marketplace database directly and re-derives the two numbers the
# Python marketplace reports on every page: "issued" is the sum of session
# budgets and "accounted" is the sum of buyer and seller wallet balances; after
# every purchase they must be equal. The queries are read-only, so running this
# against a live marketplace only takes a shared read lock.
#
#   PROOFPAY_DB=/var/lib/proofpay-mvp/market.db lsl tools/ledger_check.lsl
#
# Prints a human line plus one JSON line, and exits non-zero (Error) when the
# invariant does not hold, so it can be used in monitoring as is.

load sqlite
load env
load json

database = env.get("PROOFPAY_DB", "/var/lib/proofpay-mvp/market.db")

if not sqlite.available():
    Error(LedgerError: "the sqlite3 CLI is not available on this machine")
end

issued_row = sqlite.query_one(database, "SELECT COALESCE(SUM(budget),0) AS total FROM sessions")
accounted_row = sqlite.query_one(database, "SELECT COALESCE(SUM(available + locked),0) AS total FROM wallets")
if issued_row == None or accounted_row == None:
    Error(LedgerError: "database has no sessions or wallets table: " + database)
end

issued = Int(issued_row["total"])
accounted = Int(accounted_row["total"])
holds = issued == accounted

counts = {}
for table in ["sessions", "wallets", "jobs", "ledger", "receipts"]:
    row = sqlite.query_one(database, "SELECT COUNT(*) AS total FROM " + table)
    if row != None:
        counts[table] = Int(row["total"])
    end
end

last = sqlite.query_one(database, "SELECT action, amount, created FROM ledger ORDER BY id DESC LIMIT 1")
report = {"ok": holds, "issued": issued, "accounted": accounted, "difference": issued - accounted,
    "database": database, "counts": counts, "sqlite": sqlite.version()}
if last != None:
    report["last_ledger"] = {"action": String(last["action"]), "amount": Int(last["amount"])}
end

verdict = "BROKEN"
if holds:
    verdict = "HOLDS"
end
print "market invariant: " + String(issued) + " issued == " + String(accounted) + " accounted -> " + verdict
print json.encode(report)

if not holds:
    Error(LedgerError: "market invariant is broken: issued " + String(issued) + " != accounted " + String(accounted))
end
